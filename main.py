"""Entry point.

Examples (run from the project folder; CITY.json is any dataset in the
competition format, e.g. data/toronto-downtown.json):
  python3 main.py --dataset CITY.json --web                  # live dashboard, add crises in the page
  python3 main.py --dataset CITY.json                        # interactive text CLI
  python3 main.py --dataset CITY.json --generate 10 --run    # random scenario from that city, full report
  python3 main.py --dataset CITY.json --emergencies E.json --disruptions D.json --run
  python3 main.py --dataset CITY.json --fetch-weather 2025-02-16 --web   # that day's real weather

Nothing about a particular city is built in: the dataset is a required
argument, every tunable number is in loader/config_loader.py (override
with --config), and the start time is a setting (--start).
"""

import argparse
import copy
import os
import re
import sys

from core.event_bus import EventBus
from core.system import CrisisResponseSystem
from core.utils import parse_time
from loader.config_loader import ConfigError, apply_overrides, deep_merge, load_config
from loader.dataset_loader import DatasetError, load_dataset
from loader.input_loader import InputFileError, load_disruptions_file, load_emergencies_file
from loader.weather_loader import (WeatherError, city_point, fetch_open_meteo, load_snapshot,
                                   save_snapshot, weather_disruptions)
from sim.scenario_generator import generate_scenario
from sim.simulator import Simulator
from ui.cli import CrisisCLI, format_event
from ui.web import Dashboard


def build_parser():
    p = argparse.ArgumentParser(description="Urban Food Crisis Response and Routing System (team Ironcladreturns)")
    p.add_argument("--dataset", required=True, metavar="FILE",
                   help="city dataset JSON in the competition format (required)")
    p.add_argument("--emergencies", action="append", default=[], help="emergency requests JSON (repeatable)")
    p.add_argument("--disruptions", action="append", default=[], help="disruption events JSON (repeatable)")
    p.add_argument("--start", metavar="HH:MM",
                   help="clock time the city starts at when no input has a time "
                        "(default: config simulation.start_time)")
    p.add_argument("--generate", type=int, default=0, metavar="N", help="add N random emergencies (works on any dataset)")
    p.add_argument("--generate-disruptions", type=int, default=None, metavar="M", help="random disruptions (default N/2)")
    p.add_argument("--seed", type=int, default=42, help="random seed for --generate (same seed = same scenario)")
    p.add_argument("--config", help="JSON file overriding default config values")
    p.add_argument("--run", action="store_true", help="batch mode: run the scenario to the end and print a report")
    p.add_argument("--until", help="batch mode: stop at this clock time (HH:MM)")
    p.add_argument("--quiet", action="store_true", help="batch mode: print only the final report")
    p.add_argument("--web", action="store_true",
                   help="live web dashboard (map, queue, events) instead of the text CLI")
    p.add_argument("--port", type=int, default=8765, help="port for --web (default 8765)")
    p.add_argument("--paused", action="store_true",
                   help="--web: start with the clock paused (default: the city runs live)")
    p.add_argument("--weather", metavar="FILE", help="replay a saved weather snapshot (see --fetch-weather)")
    p.add_argument("--fetch-weather", metavar="YYYY-MM-DD",
                   help="download that day's real hourly weather for the dataset's city from Open-Meteo "
                        "(free, no key), save it in the current folder, and use it")
    return p


def earliest_time(records, keys):
    """Earliest time found in raw records (None if none): where the scenario starts."""
    times = []
    for raw in records:
        if isinstance(raw, dict):
            for key in keys:
                try:
                    times.append(parse_time(raw[key]))
                    break
                except (KeyError, ValueError):
                    continue
    return min(times) if times else None


def weather_events(args, dataset, start):
    """Weather disruptions from --fetch-weather (one download, then saved) or --weather (saved file)."""
    if args.fetch_weather:
        lat, lng = city_point(dataset)
        snapshot = fetch_open_meteo(lat, lng, args.fetch_weather)
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", dataset.id)
        path = os.path.join(os.getcwd(), f"weather_{safe_id}_{args.fetch_weather}.json")
        save_snapshot(snapshot, path)
        shown = os.path.relpath(path)
        print(f"Saved Open-Meteo weather to {shown} (replay it offline with --weather {shown})")
    else:
        snapshot = load_snapshot(args.weather)
    events = weather_disruptions(snapshot, start)
    print(f"Weather {snapshot.get('date', '')}: {len(events)} condition change(s): "
          + ", ".join(f"{e['time']} {e['condition']}" for e in events))
    return events


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.start is not None:
            config = apply_overrides(deep_merge(config["_overrides"], {"simulation": {"start_time": args.start}}))
        dataset = load_dataset(args.dataset, config)
    except (ConfigError, DatasetError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    default_start = parse_time(config["simulation"]["start_time"])
    print(f"Loaded {dataset.summary()}")
    for warning in dataset.warnings:
        print(f"  warning: {warning}")

    # Inputs: embedded in the dataset (if any) + files + generated. None of
    # them is required: with no inputs the city starts empty and waits for
    # requests typed into the CLI or the dashboard.
    try:
        emergencies = list(dataset.embedded_emergencies)
        disruptions = list(dataset.embedded_disruptions)
        for path in args.emergencies:
            emergencies += load_emergencies_file(path)
        for path in args.disruptions:
            disruptions += load_disruptions_file(path)
    except InputFileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.generate:
        n_dis = args.generate_disruptions if args.generate_disruptions is not None else args.generate // 2
        gen_e, gen_d = generate_scenario(dataset, default_start, args.generate, n_dis, seed=args.seed)
        emergencies += gen_e
        disruptions += gen_d
        print(f"Generated {len(gen_e)} emergencies and {len(gen_d)} disruption events (seed {args.seed})")
    if args.weather or args.fetch_weather:
        em_start = earliest_time(emergencies, ("submitted_at", "submission_time", "submitted", "time"))
        dis_start = earliest_time(disruptions, ("time", "at"))
        candidates = [t for t in (em_start, dis_start) if t is not None]
        # Same rule as the simulator: the earliest timed input, else the configured start.
        start = min(candidates) if candidates else default_start
        try:
            disruptions += weather_events(args, dataset, start)
        except WeatherError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    def make_world():
        """A fresh system + simulator with every input scheduled. Called once
        now, and again by the dashboard's Reset button (the dataset is reloaded
        because a run changes hub stock and vehicle positions)."""
        # raise_handler_errors=False: in live use one failing subscriber must not stop the system
        fresh = CrisisResponseSystem(load_dataset(args.dataset, config), config,
                                     bus=EventBus(raise_handler_errors=False))
        simulator = Simulator(fresh)
        for raw in copy.deepcopy(emergencies):
            simulator.schedule_emergency(raw)
        for raw in copy.deepcopy(disruptions):
            simulator.schedule_disruption(raw)
        return fresh, simulator

    system, sim = make_world()
    print(f"Scheduled {len(emergencies)} emergencies and {len(disruptions)} disruptions")

    if args.web:
        try:
            Dashboard(system, sim, make_world=make_world).serve(port=args.port, autoplay=not args.paused)
        except OSError as exc:  # e.g. the port is already in use
            print(f"ERROR: cannot start the dashboard on port {args.port}: {exc}", file=sys.stderr)
            return 2
        return 0

    if not args.run:
        CrisisCLI(system, sim).cmdloop()
        return 0

    if not args.quiet:
        system.bus.subscribe("*", lambda event: print(format_event(event)))
    until = None
    if args.until:
        try:
            until = parse_time(args.until)
        except ValueError as exc:
            print(f"ERROR: --until {exc}", file=sys.stderr)
            return 2
    sim.run(until=until)
    print()
    print(sim.report())
    if system.bus.handler_errors:
        print(f"WARNING: {len(system.bus.handler_errors)} event handler errors: {system.bus.handler_errors[:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

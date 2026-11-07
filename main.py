
"""Entry point.

Examples (run from the project folder):
  python3 main.py --demo --run                 # Dataset 1 + sample scenario, print everything
  python3 main.py --demo                       # same scenario, interactive CLI
  python3 main.py --demo --web                 # same scenario, live web dashboard (http://localhost:8765)
  python3 main.py --dataset path/to/dataset2.json --generate 10 --run
  python3 main.py --dataset X --emergencies E.json --disruptions D.json --run

The dataset path is only an argument: Dataset 2 needs no code change.
"""

import argparse
import os
import sys

from core.event_bus import EventBus
from core.system import CrisisResponseSystem
from core.utils import parse_time
from loader.config_loader import ConfigError, load_config
from loader.dataset_loader import DatasetError, load_dataset
from loader.input_loader import InputFileError, load_disruptions_file, load_emergencies_file
from sim.scenario_generator import generate_scenario
from sim.simulator import Simulator
from ui.cli import CrisisCLI, format_event
from ui.web import Dashboard

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_EMERGENCIES = os.path.join(HERE, "samples", "emergencies.json")
SAMPLE_DISRUPTIONS = os.path.join(HERE, "samples", "disruptions.json")


def build_parser():
    p = argparse.ArgumentParser(description="Urban Food Crisis Response and Routing System (team Ironcladreturns)")
    p.add_argument("--dataset", default=os.path.join(HERE, "data", "toronto-downtown.json"),
                   help="city dataset JSON (default: Dataset 1, downtown Toronto)")
    p.add_argument("--emergencies", action="append", default=[], help="emergency requests JSON (repeatable)")
    p.add_argument("--disruptions", action="append", default=[], help="disruption events JSON (repeatable)")
    p.add_argument("--demo", action="store_true", help="also load samples/emergencies.json + samples/disruptions.json")
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
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        dataset = load_dataset(args.dataset, config)
    except (ConfigError, DatasetError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Loaded {dataset.summary()}")
    for warning in dataset.warnings:
        print(f"  warning: {warning}")

    # raise_handler_errors=False: in live use one failing subscriber must not stop the system
    system = CrisisResponseSystem(dataset, config, bus=EventBus(raise_handler_errors=False))
    sim = Simulator(system)

    # Inputs: embedded in the dataset (if any) + files + generated.
    emergency_files = list(args.emergencies) + ([SAMPLE_EMERGENCIES] if args.demo else [])
    disruption_files = list(args.disruptions) + ([SAMPLE_DISRUPTIONS] if args.demo else [])
    try:
        emergencies = list(dataset.embedded_emergencies)
        disruptions = list(dataset.embedded_disruptions)
        for path in emergency_files:
            emergencies += load_emergencies_file(path)
        for path in disruption_files:
            disruptions += load_disruptions_file(path)
    except InputFileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.generate:
        n_dis = args.generate_disruptions if args.generate_disruptions is not None else args.generate // 2
        gen_e, gen_d = generate_scenario(dataset, args.generate, n_dis, seed=args.seed)
        emergencies += gen_e
        disruptions += gen_d
        print(f"Generated {len(gen_e)} emergencies and {len(gen_d)} disruption events (seed {args.seed})")
    for raw in emergencies:
        sim.schedule_emergency(raw)
    for raw in disruptions:
        sim.schedule_disruption(raw)
    print(f"Scheduled {len(emergencies)} emergencies and {len(disruptions)} disruptions")

    if args.web:
        try:
            Dashboard(system, sim).serve(port=args.port)
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

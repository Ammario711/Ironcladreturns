"""Build a source-only submission ZIP; never bundle runtime inputs or secrets."""

from pathlib import Path
import zipfile

TEAM = "Ironcladreturns"
HERE = Path(__file__).resolve().parent
OUT = HERE / f"{TEAM}_Programming_Code.zip"
ROOT_FILES = ("main.py", "traffic_loader.py", "make_zip.py", "README.md",
              "DECISIONS.md", "PROGRESS.md", "Dockerfile", "render.yaml", ".dockerignore")


def main():
    files = [HERE / name for name in ROOT_FILES]
    for directory in ("core", "loader", "sim", "ui", "tests"):
        files.extend((HERE / directory).rglob("*.py"))
    files.extend(HERE / "ui" / "static" / name for name in ("index.html", "app.js", "app.css"))
    files = sorted(set(files))
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"{TEAM}/{path.relative_to(HERE).as_posix()}")
    print(f"Wrote {OUT} ({len(files)} source files; no datasets or sample scenarios)")


if __name__ == "__main__":
    main()


"""Build the submission zip: python3 make_zip.py

Creates Ironcladreturns_Programming_Code.zip next to this file, containing
the project inside an "Ironcladreturns/" folder. Skips caches, old zips,
and hidden files. Re-run it after every change before submitting.
"""

import os
import zipfile

TEAM = "Ironcladreturns"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, f"{TEAM}_Programming_Code.zip")
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}


def main():
    count = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(HERE):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(files):
                if name.endswith((".zip", ".pyc")) or name.startswith("."):
                    continue
                path = os.path.join(folder, name)
                archive.write(path, os.path.join(TEAM, os.path.relpath(path, HERE)))
                count += 1
    print(f"Wrote {OUT} ({count} files)")


if __name__ == "__main__":
    main()

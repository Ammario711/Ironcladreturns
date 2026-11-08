# Submission status

The submission branch uses explicit datasets with the competition JSON structure. No bundled datasets or demo scenarios are loaded or shipped. Local and hosted HTTP startup are supported; supply the input file using --dataset or DATASET_PATH.

The original city-specific regression suite and saved traffic snapshot remain on port/tomtom-saved-traffic. This branch replaces those bundled fixtures with tests that construct temporary arbitrary datasets. Core routing, matching, scoring, state transitions and simulation implementations are preserved from the latest main commit.

Validation and launch commands are in README.md. Rebuild the submission archive with python make_zip.py after edits.

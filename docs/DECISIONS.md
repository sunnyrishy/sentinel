# Decisions

Every choice the spec left open. Format: what, why, date.

## D1. SPEC.md is a transcription of the PDF (Nov 13)
The brief arrived as a PDF, not `SPEC.md`. `docs/SPEC.md` is a hand transcription. The PDF wins if they differ.

## D2. Config paths resolve against the project root (Nov 13)
Relative paths in the YAML (`models/...`, `data/...`) resolve from the project root, not the cwd, so tools work from any directory.

## D3. `python -m sentinel` is a thin CLI (Nov 13)
It has `--help`, `--version` and `check-config`. Real work lives in `tools/*.py` as the spec lays out.

## D4. Build backend is hatchling, venv is `.venv` via uv (Nov 13)
Plain `pip install -e ".[dev]"` also works.

## D5. Git is local only (Nov 13)
Commits on the working branch. Nothing is pushed, per the operator rules.

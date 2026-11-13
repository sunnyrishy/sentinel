# Sentinel

Sound-to-sight alerts for hearing-protected and hard-of-hearing workers. Sentinel listens for reversing alarms, vehicle horns and fire alarms and shows a short alert on smart glasses (or a browser simulator of them).

Sentinel is an assistive alert, not a certified safety device. It does not prevent accidents.

Full README arrives in M8. Spec: `docs/SPEC.md`. Decisions: `docs/DECISIONS.md`.

## Install

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Test

```bash
pytest && ruff check .
```

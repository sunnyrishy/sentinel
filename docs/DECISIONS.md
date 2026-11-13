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

## D6. Gate emits only reversing_alarm (Nov 13)
The tonal gate targets pulsed backup alarms, so every gate detection has class `reversing_alarm`. Horns and fire alarms come from the classifier.

## D7. Gate needs a rising edge (Nov 13)
A burst only counts if at least 2 non-tonal frames came before it. A tone already on at the start of a stream is ignored, which keeps the gate silent on a continuous tone. Side effect: an alarm whose first 2 frames are at the very start of a file loses its first pulse.

## D8. Confirmation needs the same frequency (Nov 13)
The spec confirms on "a second valid burst" 0.6 to 1.5 s after the first. It does not say the frequency must match. I require the two bursts to be within `freq_tolerance_hz`, which rejects unrelated sounds that happen to be spaced right. Revisit if real alarms alternate between two tones.

## D9. Known limit: a continuous tone that starts mid-stream (Nov 13)
The spec fires a provisional detection at `burst_min_s` (0.25 s), before the gate can know the tone outlasts `burst_max_s`. So a tone that starts mid-stream raises one provisional alert, which policy cancels on timeout and counts as a shown-but-false alert. The gate never confirms it. The same happens at the turning points of a slow siren wail. Tests cover both: provisional is allowed, confirmation is not.

## D10. Gate time model (Nov 13)
`gate.tonal.process` is pure. It maps sample positions to times from the `t_end` of each block. `t_detect` is the end of the analysis frame that fired. The pipeline overwrites `t_detect` with the wall clock so compute time counts in latency.

## D11. Scenario module and extra event kinds (Nov 13)
`sim/scenario.py` (not in the spec layout) loads and renders scenario YAML. Besides `tonal_alarm`, `broadband_alarm`, `horn`, `fire_alarm_t3` and `clip`, I added decoy kinds `tone`, `beep` and `click` with `label: null`, used for negative tests. A scenario with `clip` events must state `origin: real` or `origin: synthetic`, so reports never guess.

## D12. Burst length and onset (Nov 13)
Burst length is (last tonal frame - first tonal frame + 1) * hop. Onset is the start of the first tonal frame, which can sit up to a window (64 ms) before the true onset.

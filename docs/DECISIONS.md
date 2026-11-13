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

## D13. Two pipeline modes (Nov 13)
`Pipeline.run()` is threaded and wall-clock (live mic, real-time replay): reader, gate worker, policy worker and a display thread, all connected by queues. `Pipeline.run_sync()` runs the same stage objects on one thread with a virtual clock (`StreamClock`: last frame time plus compute elapsed). Tests and `tools/evaluate.py` use sync mode so results are deterministic and fast. In sync mode latency is virtual stream time plus real compute time.

## D14. Display isolation (Nov 13)
`AsyncDisplay` runs every `show` and `clear` on a helper thread with a timeout (default 1 s). A call that times out is abandoned (daemon thread), counted in metrics, and the next command proceeds. If the command queue fills, the oldest command is dropped. Blink is emulated with show and clear cycles when `supports_blink` is false.

## D15. Microphone path is unverified here (Nov 13)
The build container has no microphone and no PortAudio. `AudioMicSource` is tested with a fake stream (16 kHz, native-rate fallback with resampling, overflow counting, open failure message). The first real-microphone check is a manual step listed in the README checklist. Input hardware latency is not included in software latency.

## D16. Mic opens at 16 kHz, falls back to native rate (Nov 13)
Opening at 16 kHz lets the OS or PortAudio resample. If that fails, the device default rate is used with `StreamResampler` (6th order Butterworth low-pass plus linear interpolation, stateful across blocks).

## D17. Interim alert handling in pipeline (Nov 13)
M2 uses a minimal show-once-per-cooldown handler inside `pipeline.py` so the console path works. M4 replaces it with `policy.py` as the spec describes.

## D18. Classifier onset estimate and stream position (Nov 13)
YAMNet needs a 0.975 s window, so a classifier detection cannot say exactly when the sound began. The onset estimate is the middle of the first window of the current positive streak. Estimated onsets are only used for the event log columns; `tools/evaluate.py` measures alarm-to-display latency against the scenario's ground-truth onset instead. The stage counts samples itself and reads the window that ends at the frame it is processing (`RingBuffer.window`), so results do not depend on how far ahead the capture thread has run.

## D19. Model source (Nov 13)
tfhub.dev and Kaggle were not reachable from the build environment. The model is Google's MediaPipe-hosted YAMNet float32 TFLite (classification variant, 521 classes), the class map is from `tensorflow/models`. URLs and SHA-256 are pinned in `tools/fetch_models.py` and listed in `models/README.md`. Input and output shapes were verified from the file.

## D20. Smoke detector class added to fire_alarm (Nov 13)
The spec says to consider it. `Smoke detector, smoke alarm` exists in the class map (index 393), so `fire_alarm.names` in `config/default.yaml` now lists it with `Fire alarm`. Remove it if it causes false alerts on real negatives.

## D21. Classifier fires on every qualifying hop (Nov 13)
While the hits-of-windows condition holds, the stage emits a Detection on every hop. The policy treats repeats as TTL extensions, so no extra alerts appear.

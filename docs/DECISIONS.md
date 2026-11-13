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

## D22. Policy edge cases the spec leaves open (Nov 13)
- **Lower priority while a higher alert is on screen.** The event is logged immediately with `cancel_reason='superseded'`, never shown, and its class enters cooldown.
- **Replacement.** When a higher priority alert replaces a shown one, the replaced event gets `cancel_reason='superseded'` even if it was confirmed, and its class enters cooldown. The display receives one `show` that replaces the old alert, not a clear first.
- **Cooldown start.** Cooldown starts when the alert clears (TTL end, timeout, or replacement), measured from that moment, not from the first detection. A cancelled provisional also starts cooldown, so a flapping source cannot retrigger every second.
- **Stage label.** Union of the stages that contributed while the event was active: `gate`, `classifier` or `both`. It can upgrade from `gate` to `both` after confirmation.
- **A non-provisional detection with no active alert** (classifier, or a gate pulse that arrives after cooldown) creates a confirmed event and shows the alert.
- **TTL shorter than the confirm timer** still ends an unconfirmed alert as `timeout`.

## D23. Event ids (Nov 13)
Ids use the spec format `e_000123` and continue from the highest stored id, so a database shared across runs never collides.

## D24. End of a source (Nov 13)
When a replay source ends on its own, the pipeline fast-forwards the policy clock by `max(confirm_timeout_s, ttl_s)` so every provisional alert ends as confirmed or cancelled in the log. On Ctrl+C it does not, and it clears the display instead, so an interrupted run never invents a `timeout`.

## D25. Log writes are asynchronous (Nov 13)
Inserts and updates go through `AsyncEventLog` on their own thread, in order, so the policy worker never waits for SQLite. Reads (dashboard) use the locked connection directly. `Pipeline` flushes the writer at the end of a run.

## D26. Event log defaults (Nov 13)
`run_replay` and `run_live` write to `eventlog.path` from the config. Replays are tagged `source=synthetic` or `source=replay`, never `live`. Use `--db PATH` for a throwaway database.

## D27. Who emits which WebSocket message (Nov 13)
The display adapter (`display_sim.py`, later `display_bridge.py`) emits `alert` and `clear`. The pipeline emits `event` messages (create, confirm, cancel, latencies, acknowledge) through listeners. The dashboard server adds `stats` every 2 s. This keeps the architecture rule that the display adapter is the only thing that draws, so a phone bridge receives exactly what the glasses would.

## D28. Dashboard runs inside the tool process (Nov 13)
`tools/run_live.py` and `tools/run_replay.py` start uvicorn on a background thread (`sentinel.runtime.start_runtime`) when a `sim` or `bridge` display is chosen, or with `--serve`. The default config keeps `display.adapter: console` as in the spec; use `--sim` (adds the simulator to the display list) or `--display console,sim`. `--serve` implies real-time pacing so the browser timers match the audio.

## D29. Simulator behaviour (Nov 13)
- The simulator blinks natively, so `SimDisplay.capabilities().supports_blink` is always true and the pipeline does not emulate blink for it.
- Clicking an alert acknowledges the event but does not clear it. It stays until `clear` or its `ttl_ms`.
- A page opened mid-alert receives the active alert with its remaining time.
- The `RECORDING` badge is driven by `GET /api/status` (`source` is not `live`). The same value is stored in every event's `source` column.
- `GET /api/per_hour` and `GET /api/events.csv` are additions to the endpoints in the spec, for the near-miss view and CSV export.
- Alert text is drawn at 12 characters per line (the assumed display size) and never wraps.

## D30. Browser check (Nov 13)
The page was driven with the preinstalled Chromium against a live replay: alert text, RECORDING badge, row update, click to acknowledge, kiosk layout and no console errors. This is a manual check, not part of the pytest suite.

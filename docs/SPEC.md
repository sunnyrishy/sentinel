# Sentinel: Project Brief and Engineering Spec

Nov 13, 2026 · @Sunny

> Markdown export of the original PDF (26 pages). The PDF is the source of truth if the two differ.

## Summary and locked decisions

Sentinel detects safety-critical sounds (reversing alarms, horns, fire alarms) and shows a 2-word alert on smart glasses for workers whose hearing is blocked by protection or reduced by loss. The core software gets built and tested before Nov 20, then connects to the MicroLumin glasses at the Worn hackathon (Nov 20 to 21, City Tech, Brooklyn).

- **Product:** sound-to-sight alerts for hearing-protected and hard-of-hearing workers.
- **Scope:** 3 alert classes, 1 display behavior, 1 near-miss log and dashboard.
- **Approach:** a fast tonal-alarm gate plus a pretrained sound classifier, with a hardware-independent core and one thin display adapter.
- **Pre-build:** core software before the event, hardware integration on event day. Hacking starts Fri Nov 20 at 8:00 PM and code freeze is Sat Nov 21 at 2:00 PM.
- **Rules:** pre-built work is allowed per the organizers as understood by Sunny. Keep their confirmation in writing and say in the pitch what was built beforehand.

## Instructions for the coding agent

This document is the single source of truth for building the Sentinel software. Read all of it before writing code. Where it is silent, pick the simplest option, record the choice in `docs/DECISIONS.md` and keep going.

1. **Binding sections:** Requirements and scope, Interfaces and data contracts, Detection algorithms, Configuration defaults, and Build order with acceptance criteria. Everything else is context and evidence.
2. **Hardware is mostly unknown and details will arrive slowly.** Never invent SDK calls. All hardware-specific code lives in `src/sentinel/adapters/` behind the two interfaces, with stubs that raise `NotImplementedError` and carry TODO notes.
3. **Offline at runtime.** No network calls once models are downloaded.
4. **No raw audio stored by default.** Recording is allowed only behind an explicit `--record` flag, used to build test sets.
5. **Honest numbers only.** Reported metrics come from `tools/evaluate.py` run on held-out real recordings. Anything measured on synthetic audio is labeled synthetic and used for tests, never for claims.
6. **Data licensing.** Use only CC0 or CC-BY audio from public sources, list every clip in `data/ATTRIBUTIONS.md`, and never commit audio files larger than a few MB.
7. **Work in small commits.** Run tests after each milestone and keep `README.md` accurate so someone new can run the demo in under 10 minutes.
8. **Ask only when blocked** by information that only the user can provide, and ask one specific question. Otherwise decide, log it and continue.
9. **Performance:** measure first. If the latency targets are missed in Python, profile, then port only the hot path (the gate's spectral analysis) to C++ behind the same function signature.
10. **Style:** type hints everywhere, ruff clean, pytest for tests, no em dashes in docs, comments or UI text.

## Hardware and SDK

No public MicroLumin SDK was found, so the design assumes the organizers' docs arrive late and keeps all hardware behind one adapter. The facts below come from Chinese-language press and reviews of the MLVISION M6 series, so treat them as indicative until the real docs confirm them.

| Item | What the sources say | Confidence |
|---|---|---|
| M6 Air base | 36.8 g AI camera glasses, Sony IMX681 12MP sensor, voice AI (Tencent News) | Medium |
| Display modules | Magnetic JBD Micro-LED waveguide, monochrome green or full color (same article) | Medium |
| M6 Display | About 48 g, image floats at upper right of view, readable only from straight on (Sohu review) | Medium |
| Phone link | Connects straight to the MLVISION phone app, which pushes teleprompter files and sets brightness and screen-off time (OFweek) | Medium |
| Software design | Device, edge and cloud tiers, with heavy AI work offloaded to phone and cloud (OFweek) | Medium |
| Audio | Described as an audio, display and camera glasses (Jiemian) | Low |
| Limits | Weaker in extreme light, battery compromises (Sohu review) | Medium |
| Event model | Not confirmed. Another vendor sells an unrelated "M6 AR Glass" on Android 10, so ignore its specs | None |

What this means for the build:

1. Run detection on a laptop or phone and treat the glasses as a display. This matches the vendor's own device, edge and cloud split.
2. The likely integration path is the phone app or an Android SDK pushing text to the display. Confirm before writing any glasses code.
3. Whether developers can read the glasses' microphone is unknown, so plan for an external mic from day one.

## Questions for the organizers and MicroLumin

Send these through the Luma event page (message the hosts) and ask for a MicroLumin developer contact. The answers decide how much of the adapter can be written before Nov 20.

- [ ] Which exact model do teams get: M6 Air plus which display module (monochrome green or full color)?
- [ ] What form is the SDK: Android library, BLE protocol, cloud API or phone app only? Can we see the docs or a sample app now?
- [ ] Display: can we send arbitrary text and simple icons? Maximum update rate, character limit, and can brightness be set in code?
- [ ] Audio: does our app receive the glasses' microphone stream (raw PCM, sample rate, channels)? How many mics are there?
- [ ] Connectivity: is a phone required, or can a laptop connect directly? Is Wi-Fi at the venue reliable, and can we use our own hotspot?
- [ ] Typical latency from our app to text appearing on the display?
- [ ] Is there a simulator or loaner unit so we can test before Nov 20?
- [ ] Rules: is pre-built code allowed, are open-source libraries and pretrained models allowed, and can we bring our own mics and a speaker?
- [ ] Demo: is playing loud alarm sounds at our table allowed during judging?

## Alarm sound facts

Backup alarms come in two families, tonal and broadband, and the detector has to handle both. Tonal alarms pulse at a steady rate, so a cheap signal-processing gate can catch them fast. Broadband alarms need the classifier.

| Fact | Value | Source |
|---|---|---|
| Sound level | 87 to 112 dB(A) at about 4 ft, by SAE J994 type (B 107, C 97, D 87) | Federal Signal |
| Pulse rate | Roughly 60 to 80 pulses per minute, equal on and off time | Federal Signal, Lawson |
| Tonal pitch | One Brigade tonal model lists 1.9 kHz (single data point, confirm with recordings) | Brigade |
| Broadband alarms | Multi-frequency "white noise" types, attenuated above 4 kHz | ECCO |
| Smart alarms | Adjust their output to about 5 dB above ambient noise | ECCO |
| Not required everywhere | OSHA does not universally require forklift backup alarms, and workers may not know which direction a sound comes from | Liftron |
| Hearing protection | Traditional earplugs and earmuffs reduce all sound equally, hiding alarms and colleagues | Sensear |

Design consequences:

- The tonal gate looks for a narrow spectral peak in roughly 0.8 to 3 kHz, then checks for on and off pulses of about 0.3 to 1 s. The band is a working assumption and must be checked against real recordings.
- Broadband alarms, horns and fire alarms go to the classifier. Collect test clips of both alarm families.
- Check the model card for YAMNet's analysis window length, because it sets the classifier's minimum delay.

## Requirements and scope

Version 1 is three alert classes, one display behavior and a near-miss log. The targets below are proposals to validate with measurements, not promises.

| ID | Requirement | Proposed target |
|---|---|---|
| R1 | Detect reversing alarm, vehicle horn, fire alarm | At least 90% recall on our own test clips at the demo noise level |
| R2 | Alarm-to-display latency | Median under 500 ms for tonal alarms, under 1.5 s for classifier-only |
| R3 | False alerts in loud noise | Under 1 per 10 minutes in a noisy-room test |
| R4 | Alert design | Max 2 words plus arrow or NEARBY, auto-clear after 3 s, 5 s cooldown per class |
| R5 | Connectivity | Runs fully offline |
| R6 | Privacy | No raw audio stored, only event metadata |
| R7 | Hardware independence | Swap the display adapter without touching detection code |
| R8 | Event log | Time, class, confidence, latency, acknowledged flag, shown on a dashboard |

Out of scope for version 1: speech recognition, camera vision, other languages, user accounts, cloud sync. Direction finding with two mics is a stretch goal.

Pitch wording rule: Sentinel is an assistive alert, not a certified safety device, and the pitch never claims it prevents accidents.

## Architecture and fallback ladder

Only the two adapters touch hardware, so detection can be built and tested now.

```
Input adapter (L0: glasses mic, L1: external mic)
   -> Sentinel core (runs and tests without glasses)
        Rolling audio buffer
          -> 1. Tonal gate (fast, pulsed alarms)
          -> 2. Classifier (YAMNet confirms class)
        Policy engine (alert tiers, cooldown, dedupe) -> Display adapter (glasses or simulator)
        Event log (no raw audio stored)               -> Dashboard (near-miss feed)
```

Fallback ladder: L0 glasses mic and glasses display. L1 external mic, glasses display. L2 labeled recording, never presented as live.

The highlighted boxes (input adapter, display adapter) are the only parts that change on event day. The display adapter gets an on-screen simulator so the whole core can be built and tested without the glasses. The tonal gate fires the first alert and the classifier confirms or cancels it.

## Tech stack and repo layout

These defaults resolve the open decisions so coding can start now. Change them only with a note in `docs/DECISIONS.md`.

| Decision | Default | Notes |
|---|---|---|
| Core language | Python 3.11 or newer | Fastest to build and test before the event. Keep the gate's spectral code pure and isolated so it can be ported to C++ if profiling demands it |
| Packaging | `pyproject.toml`, `src/` layout, virtual environment (`uv` or `pip`) | One command to install, one to run tests |
| Audio I/O | `sounddevice` for the live mic, `soundfile` for wav files | |
| DSP | `numpy`, `scipy` | |
| Classifier runtime | TFLite through `ai-edge-litert` or `tflite-runtime`, falling back to `tensorflow` if neither installs | Pick whichever installs on the dev machine and note it in `README.md` |
| Classifier model | YAMNet, TFLite classification variant plus its class map CSV | `tools/fetch_models.py` downloads them. Find the official source, then record the URL and a checksum in `models/README.md`. Verify the input size from the model card |
| Config and types | `pydantic` v2 and YAML | Fail loudly on unknown keys or class names |
| Dashboard | FastAPI, uvicorn, WebSocket, one static HTML page | No frontend build step, no framework |
| Storage | SQLite through the standard library | |
| Tests and lint | `pytest`, `ruff` | |
| Processing host | The developer's laptop | If the SDK turns out to be phone-only, use the bridge design in the integration section |

Repository layout:

```
sentinel/
  README.md
  pyproject.toml
  config/default.yaml
  models/                       # README.md plus downloaded model files (gitignored)
  data/                         # gitignored audio; tracked: ATTRIBUTIONS.md, manifests
  docs/
    SPEC.md                     # this document, exported as Markdown
    DECISIONS.md                # every choice the spec left open
    SDK_NOTES.md                # hardware facts as they arrive
    RECORDING_GUIDE.md          # how to record the real test set
  src/sentinel/
    types.py                    # dataclasses and enums from this spec
    config.py                   # pydantic models, YAML loading, config hash
    buffer.py                   # thread-safe ring buffer
    gate/tonal.py               # stage 1
    classify/yamnet.py          # stage 2
    policy.py                   # alert state machine
    eventlog.py                 # SQLite events
    metrics.py                  # latency probes and percentiles
    pipeline.py                 # wires sources, stages, policy, display, log
    adapters/
      base.py                   # AudioSource and DisplayAdapter protocols
      audio_mic.py              # sounddevice
      audio_file.py             # wav replay, deterministic
      audio_glasses.py          # STUB until the SDK is known
      display_console.py
      display_sim.py            # WebSocket to the browser simulator
      display_bridge.py         # WebSocket publisher for a phone bridge
      display_microlumin.py     # STUB until the SDK is known
    dashboard/server.py         # FastAPI app
    dashboard/static/index.html
    sim/synth.py                # synthetic alarms, noise, mixing at a target SNR
  tools/
    fetch_models.py  run_live.py  run_replay.py  evaluate.py
    make_test_set.py  latency_probe.py  hw_smoke_test.py  demo.py  bridge_echo.py
  scenarios/                    # YAML scenario files
  tests/
```

## Interfaces and data contracts

These types are binding. Audio inside the core is always mono, 16 kHz, float32 in the range -1 to 1; each source resamples to that.

```python
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional, Protocol
import numpy as np

SAMPLE_RATE = 16_000


class AlertClass(str, Enum):
    REVERSING_ALARM = "reversing_alarm"
    VEHICLE_HORN = "vehicle_horn"
    FIRE_ALARM = "fire_alarm"


@dataclass(frozen=True)
class AudioFrame:
    samples: np.ndarray  # shape (n,), float32
    t_end: float  # time.monotonic() at the last sample


class AudioSource(Protocol):
    sample_rate: int  # equals SAMPLE_RATE after resampling

    def frames(self) -> Iterator[AudioFrame]: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class Detection:
    alert_class: AlertClass
    stage: str  # 'gate' or 'classifier'
    score: float  # 0 to 1
    provisional: bool  # True for the gate's first, unconfirmed hit
    t_onset: float  # monotonic time of the estimated sound onset
    t_detect: float  # monotonic time the detector fired


@dataclass(frozen=True)
class Alert:
    event_id: str
    alert_class: AlertClass
    tier: int  # 1 urgent, 2 notice
    line1: str  # for example 'BACKUP ALARM'
    line2: Optional[str]  # 'NEARBY', 'LEFT', 'RIGHT', 'BEHIND' or None
    blink: bool
    ttl_s: float


@dataclass(frozen=True)
class DisplayCaps:
    lines: int = 2
    max_chars_per_line: int = 12  # ASSUMPTION until the SDK docs confirm
    color: str = "mono_green"  # ASSUMPTION: 'mono_green' or 'full'
    supports_blink: bool = False  # emulate with show/clear cycles if False


class DisplayAdapter(Protocol):
    def capabilities(self) -> DisplayCaps: ...
    def show(self, alert: Alert) -> None: ...
    def clear(self, event_id: Optional[str] = None) -> None: ...
    def close(self) -> None: ...
```

Adapters must truncate text to their capabilities, and a slow display must never block detection: run `show` and `clear` on their own thread with a timeout.

**Alert wording.** Direction is unknown in version 1, so line 2 is always `NEARBY` and no text claims a direction.

| Class | Tier | Line 1 | Line 2 |
|---|---|---|---|
| reversing_alarm | 1 | BACKUP ALARM | NEARBY |
| vehicle_horn | 1 | VEHICLE HORN | NEARBY |
| fire_alarm | 1 | FIRE ALARM | NEARBY |

**WebSocket protocol** (`ws://127.0.0.1:8765/ws`), JSON text frames. The simulator page and any future phone bridge both subscribe to it.

```
{"type": "alert", "event_id": "e_000123", "line1": "BACKUP ALARM", "line2": "NEARBY", "tier": 1, "blink": true, "ttl_ms": 3000}
{"type": "clear", "event_id": "e_000123"}
{"type": "event", "event": {"id": "e_000123", "alert_class": "reversing_alarm", "confirmed": true}}
{"type": "stats", "events_total": 12, "p50_ms": 410, "p95_ms": 880}
```

**HTTP endpoints:** `GET /` (dashboard and simulator), `GET /api/events?limit=100`, `GET /api/stats`, `POST /api/events/{id}/ack`.

**Event log schema** (SQLite):

```sql
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  ts_wall TEXT NOT NULL,             -- ISO 8601 UTC
  alert_class TEXT NOT NULL,
  stage TEXT NOT NULL,               -- gate | classifier | both
  score REAL NOT NULL,
  confirmed INTEGER NOT NULL,        -- 0 or 1
  cancel_reason TEXT,                -- timeout | superseded | NULL
  t_onset REAL, t_detect REAL, t_display REAL,   -- monotonic seconds
  processing_ms REAL,                -- t_display - t_detect
  alarm_to_display_ms REAL,          -- t_display - t_onset
  acknowledged INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL,              -- live | replay | synthetic
  config_hash TEXT NOT NULL
);
```

Two latencies exist. Processing latency (detector fires to `show` returns) and alarm-to-display latency (sound onset to `show` returns) are measured in software. True end-to-end latency to visible photons on the glasses can only be measured on hardware, for example with slow-motion phone video, so `tools/latency_probe.py` should print timestamps that make that measurement easy.

## Detection algorithms

All numeric values below are starting defaults that live in the config file. They come from public alarm specifications and assumptions, so they must be tuned on real recordings.

**Stage 1: tonal gate (`gate/tonal.py`)** catches pulsed tonal backup alarms quickly. Input is the rolling buffer; output is `Detection` objects with `stage='gate'`.

1. Short-time Fourier transform with a Hann window, 1024 samples (64 ms) and a hop of 256 samples (16 ms) at 16 kHz.
2. Per frame, look for the strongest bin inside the search band (default 800 to 3000 Hz). The frame is tonal if that peak is at least `peak_to_median_db` (default 15 dB) above the median level of the band and is a local maximum against its neighbors.
3. A tone is stable when the peak frequency stays within `freq_tolerance_hz` (default 50 Hz) of its reference for at least `min_stable_frames` consecutive tonal frames (default 5, allowing gaps of up to 2 frames).
4. A burst is a run of stable tonal frames. It is valid when its length is between `burst_min_s` (0.25 s) and `burst_max_s` (1.0 s). A tone that outlasts `burst_max_s` is treated as continuous and ignored by the gate.
5. **Provisional detection:** emit `Detection(provisional=True)` as soon as a burst reaches `burst_min_s`. Estimate onset as the burst's first frame minus half a window.
6. **Confirmation:** a second valid burst whose onset follows the first onset by 0.6 to 1.5 s (about 40 to 100 pulses per minute) emits `Detection(provisional=False)`. Real alarms are specified at roughly 60 to 80 pulses per minute with equal on and off time.
7. Wideband "white noise" alarms are not tonal, so the gate ignores them and the classifier covers them.

Keep this module pure: a function from a block of float32 samples and a small state object to a list of detections, with no I/O, so it is easy to test and to port to C++.

**Stage 2: classifier (`classify/yamnet.py`)** confirms the class and covers horns, fire alarms and wideband alarms.

1. Keep the most recent model-sized window of audio (verify the exact input length in the model card; the classification variant is expected to take a fixed window of about one second at 16 kHz).
2. Every `hop_s` (default 0.25 s) run inference and take the 521 class scores.
3. For each Sentinel class, take the maximum score over the configured AudioSet class names (matched by exact display name from the class map).
4. A class is detected when its score is at least its threshold in at least `hits` of the last `windows` inferences (default 2 of 3). Emit `Detection(provisional=False, stage='classifier')`.
5. At startup, fail with a clear message if any configured class name is missing from the class map, and list the closest names.

**Fusion and alert state machine (`policy.py`).** State is tracked per class: IDLE, PROVISIONAL, CONFIRMED, COOLDOWN.

- A provisional gate detection shows the alert immediately, creates an event with `confirmed=0` and starts a timer of `confirm_timeout_s` (default 1.6 s).
- The event becomes confirmed when the gate reports a second valid pulse or the classifier detects the same class before the timer ends. The stage is recorded as `gate`, `classifier` or `both`.
- If the timer ends unconfirmed, clear the display and record `cancel_reason='timeout'`. These count as shown-but-false alerts in the metrics.
- A classifier detection with no provisional alert shows the alert and creates a confirmed event.
- Detections of the same class while an alert is active extend its time to live. After the alert clears, the class is in cooldown for `cooldown_s` (default 5 s) and creates no new event.
- Only one alert is on the display at a time. Priority is fire alarm, then reversing alarm, then vehicle horn. A higher priority replaces a lower one and the replaced event is logged with `cancel_reason='superseded'`. Every event is still logged.
- All tier 1 alerts blink on show if the display supports it, otherwise emulate it with show and clear cycles.

**Concurrency.** The capture callback only writes into the ring buffer and a queue. Separate workers run the gate, the classifier, the policy, the display calls and the log writer, connected by queues. All timestamps use `time.monotonic()`.

## Configuration defaults

This is the starting `config/default.yaml`. Validate it with pydantic, reject unknown keys, and store a hash of the active config in every event.

```yaml
audio:
  sample_rate: 16000
  device: null              # sounddevice name or index, null = system default
  frame_ms: 32
buffer:
  seconds: 10
gate:
  enabled: true
  n_fft: 1024
  hop: 256
  band_hz: [800, 3000]      # ASSUMPTION, tune on real recordings
  peak_to_median_db: 15
  freq_tolerance_hz: 50
  min_stable_frames: 5
  burst_min_s: 0.25
  burst_max_s: 1.0
  pulse_period_s: [0.6, 1.5]
  confirm_timeout_s: 1.6
classifier:
  enabled: true
  model_path: models/yamnet_classification.tflite   # final name set by fetch_models.py
  class_map_path: models/yamnet_class_map.csv
  hop_s: 0.25
  smoothing: {hits: 2, windows: 3}
  classes:                  # names must match the class map exactly
    reversing_alarm:
      names: ['Reversing beeps']
      threshold: 0.30
    vehicle_horn:
      names: ['Vehicle horn, car horn, honking', 'Air horn, truck horn']
      threshold: 0.30
    fire_alarm:
      names: ['Fire alarm']
      threshold: 0.30
policy:
  cooldown_s: 5.0
  ttl_s: 3.0
  blink: true
  priority: [fire_alarm, reversing_alarm, vehicle_horn]
display:
  adapter: console          # console | sim | bridge | microlumin
  caps:
    lines: 2
    max_chars_per_line: 12  # ASSUMPTION
    color: mono_green       # ASSUMPTION
    supports_blink: false
dashboard:
  enabled: true
  host: 127.0.0.1
  port: 8765
eventlog:
  path: data/events.sqlite
recording:
  enabled: false            # only true with the --record flag
  dir: data/recordings
```

Thresholds of 0.30 are placeholders. `tools/evaluate.py --sweep-thresholds` should print precision and recall per threshold so they can be set from data. If the AudioSet class list also has a smoke detector class, check the class map and consider adding it to `fire_alarm`.

## Synthetic data and test harness

Real recordings will arrive later, so the agent builds a synthetic generator first and uses it for unit tests and development. Synthetic stand-ins are approximations and are never used for reported performance.

**Generators (`sim/synth.py`):**

- `tonal_alarm(freq_hz, pulses_per_min, duration_s, duty=0.5, harmonics=0)`: sine bursts with short fades to avoid clicks. Default 1900 Hz and 70 pulses per minute.
- `broadband_alarm(pulses_per_min, duration_s, band_hz=(300, 4000))`: band-limited noise bursts, a stand-in for "white noise" alarms.
- `horn(duration_s)`: a sustained two-tone signal, and `fire_alarm_t3(duration_s)`: three 0.5 s beeps with 0.5 s gaps, then a 1.5 s pause, repeated.
- Noise: white, pink, brown, and a crude babble made by summing filtered noise bursts.
- `mix(signal, noise, snr_db)`: scales the noise so that the signal's RMS over its active segments sits at the requested SNR, and returns the ground-truth onset and end times.

**Scenario file (`scenarios/*.yaml`)**, which `tools/run_replay.py` and `tools/evaluate.py` consume:

```yaml
name: tonal_snr_0db
duration_s: 60
seed: 7
noise: {type: pink, level_dbfs: -30}
events:
  - {kind: tonal_alarm, freq_hz: 1900, pulses_per_min: 70, start_s: 10, duration_s: 6, snr_db: 0, label: reversing_alarm}
  - {kind: clip, path: data/clips/horn_01.wav, start_s: 30, snr_db: 5, label: vehicle_horn}
```

**Scoring rules.** An event counts as detected if a confirmed detection of the right class occurs between its start and its end plus 1.0 s. A false alert is any alert that was shown with no matching event, whether or not it was later confirmed. Report them separately: confirmed false alerts and cancelled provisional alerts.

**Evaluation outputs (`tools/evaluate.py`):** recall per class and SNR, false alerts per hour on negative-only scenarios, processing and alarm-to-display latency (median and 95th percentile), CPU use, and a Markdown report in `reports/`. Every report states whether the audio was synthetic or real.

**Unit tests the agent must write (`tests/`):**

- The gate detects a synthetic 1900 Hz, 70 pulses per minute alarm at +10 dB SNR in white and pink noise in at least 9 of 10 seeded runs, and reports a provisional detection before the confirmed one.
- The gate stays silent on a continuous tone, white noise, babble-like noise, a single click, and a lone 0.1 s beep.
- Policy tests: provisional shown then cancelled on timeout, provisional confirmed by a second pulse, classifier-only alert, cooldown, TTL extension, priority replacement, one alert on screen at a time.
- Event log tests: schema creation, inserts, updates on confirmation, queries.
- Display adapter tests: truncation to `DisplayCaps`, and that a slow `show` does not block the pipeline.
- Config tests: unknown keys and unknown class names fail with clear messages.
- Pipeline replay test: a synthetic scenario produces the expected events with latencies recorded.

**Real test set.** The agent writes `docs/RECORDING_GUIDE.md` following the protocol in the data and test plan section (at least 20 positive events per class, 2 hours of negatives, three noise levels, a held-out set recorded on a different day and in a different room), and `tools/make_test_set.py` to mix user-supplied clips with noise into scenario files plus a manifest listing each clip's license.

## Event log and dashboard

The dashboard is one static HTML page served by FastAPI, fed by the WebSocket in the contracts section. It has two jobs: stand in for the glasses until hardware exists, and tell the business story during judging.

**Glasses-view simulator panel.** A dark rectangle anchored to the upper right of a larger frame, drawn the way the real display is described: a small region, two lines of text, green on black. It shows `line1` and `line2` from `alert` messages, blinks when `blink` is true, and clears on `clear` or after `ttl_ms`. Click an alert to send the acknowledge request.

**Dashboard panels:**

- Live feed of events with time, class, stage, confirmed or cancelled, and alarm-to-display latency.
- Counters: events per class, confirmed versus cancelled, events in the last hour.
- Latency: median and 95th percentile of processing and alarm-to-display latency.
- A near-miss view: events per hour as a simple bar list, the data a safety manager would use.
- Export of the event table as CSV.

**Kiosk mode (`/?kiosk=1`):** full-screen simulator with a compact stats strip, large text, no scrollbars, for use on a laptop at the demo table.

**Replay and demo labeling.** Whenever the source is a recording or a synthetic scenario instead of the live mic, the page shows a visible `RECORDING` badge and the event `source` field says so. The demo must never present a recording as live.

**Privacy.** The log stores event metadata only. Raw audio is never written unless the `--record` flag is given, and then only to `data/recordings/`.

## Build order and acceptance criteria

Build in this order and do not start a milestone until the previous one passes.

| Milestone | Deliverable | Done when |
|---|---|---|
| M0 Scaffold | Repo layout, `pyproject.toml`, types, config loader and validation, empty test suite | `pytest` and `ruff` pass; `python -m sentinel --help` prints usage |
| M1 Synthetic audio and Stage 1 | `sim/synth.py`, `gate/tonal.py`, `tools/run_replay.py`, gate unit tests | Gate tests from the test list pass; replaying `scenarios/tonal_10db.yaml` prints a provisional then a confirmed detection |
| M2 Live pipeline | Ring buffer, mic and file sources, console display, metrics probes, `tools/run_live.py` | A beep played from a phone near the laptop prints an alert on the console; processing latency is printed per event |
| M3 Stage 2 | `classify/yamnet.py`, `tools/fetch_models.py`, class-name check | Model loads with no network; every configured class name resolves; replay on a real wav prints class scores |
| M4 Policy and log | `policy.py`, `eventlog.py`, latency fields | All policy and event log tests pass; events from a replay appear in SQLite with correct stage and cancel reason |
| M5 Dashboard and simulator | FastAPI app, static page, WebSocket display adapter, acknowledge endpoint | Starting `run_live.py` or a replay shows alerts in the browser simulator; acknowledging an alert updates the row |
| M6 Evaluation | `tools/evaluate.py`, `tools/make_test_set.py`, `docs/RECORDING_GUIDE.md`, report template | One command produces a Markdown report with recall by class and SNR, false alerts per hour, latency percentiles, and a synthetic or real label |
| M7 Hardware slots | Stub adapters, `display_bridge.py`, `tools/bridge_echo.py`, `tools/hw_smoke_test.py`, `docs/SDK_NOTES.md` | Stubs import cleanly and fail with a clear message; the bridge echo script prints alerts sent by the core |
| M8 Demo mode | `tools/demo.py`, kiosk page, README demo steps, labeled replay fallback | One command starts everything in demo mode; replay mode shows the `RECORDING` badge |

**Definition of done for the pre-event phase:** M0 to M8 complete, all tests passing, README accurate, and `docs/SDK_NOTES.md` ready for hardware facts. The evaluation report on real recordings is produced by the user after recording their own clips with the guide.

**Requirement targets** from the requirements section apply to the final evaluation: recall, latency and false-alert targets are goals to measure against, not claims to print before they are measured.

## Integration slots for hardware details that arrive later

Every unknown has a named slot, a safe default and a place to record the answer. When new facts arrive, the user pastes them into `docs/SDK_NOTES.md` and the agent changes only the code in the listed slot.

| Unknown | Plugs into | Default until known | Record in |
|---|---|---|---|
| Display API: can it show text, icons, how fast can it update | `adapters/display_microlumin.py` | Console and browser simulator | `SDK_NOTES.md` |
| Display size, characters per line, color | `DisplayCaps` in config | 2 lines of 12 characters, monochrome green (assumed) | `SDK_NOTES.md` and config |
| Glasses microphone access | `adapters/audio_glasses.py` | Laptop or USB mic through `sounddevice` | `SDK_NOTES.md` |
| Connection path: phone app, BLE, Wi-Fi, direct to laptop | `adapters/display_bridge.py` | WebSocket bridge | `SDK_NOTES.md` |
| Phone required or Android-only SDK | Architecture choice below | Laptop core with a bridge | `DECISIONS.md` |
| Update delay from our call to text on the lens | `tools/hw_smoke_test.py`, `tools/latency_probe.py` | Unmeasured | `SDK_NOTES.md` |
| Exact glasses model and display module | Documentation only | M6 series assumed from public reviews | `SDK_NOTES.md` |

**Two integration architectures.**

- **A, laptop core with a bridge (preferred).** The core runs on the laptop and publishes the alert messages from the contracts section over WebSocket. A small phone app or script subscribes and forwards each alert to the glasses through whatever the SDK offers. This keeps all detection code unchanged and is the right shape if the SDK is a phone app or Android library.
- **B, core on the phone.** Only if A cannot meet the latency target. It needs a port of the core and is a decision for event day, not for the pre-build.

The agent implements the publisher side of A (`display_bridge.py`) and a reference subscriber (`tools/bridge_echo.py`) so a real bridge can be written quickly later.

**Hardware smoke test (`tools/hw_smoke_test.py`)** does four things once an adapter exists: shows a fixed text, clears it, times a burst of 20 show and clear calls, and prints the numbers. With stubs it must exit cleanly and say which adapter is not implemented.

**Known facts to seed `SDK_NOTES.md`** (from public reviews, not confirmed by MicroLumin): the glasses pair with a phone app; the display module is a small monochrome green or full-color Micro-LED waveguide that sits in the upper right of the view and is readable straight on; the M6 Air base has a 12 MP camera; heavy AI work is expected to run on the phone or cloud. Mark every one as unconfirmed.

## Data and test plan

Every number shown on stage must come from our own recordings, made through the same microphone path we use at the event. Public datasets are only for development.

**Model.** YAMNet is a pretrained classifier with 521 classes and a TFLite version. Its class map includes Reversing beeps, Vehicle horn, Air horn, Alarm and Fire alarm. It was trained on YouTube audio, so expect to calibrate thresholds, and its overall average precision is modest, so per-class results must be measured.

**Data sources.**

| Source | Use | Notes |
|---|---|---|
| FSD50K | Development clips and background noise | 51,197 clips, 200 AudioSet classes, per-clip licenses (CC0, CC-BY, CC-BY-NC, Sampling+). Whether reversing beeps is included is unverified, so check the class list |
| Freesound search | Extra reversing-beep and horn clips | Filter to CC0 or CC-BY and keep a credit list |
| Our own recordings | The evaluation set | Play clips through the demo speaker at 1, 3 and 5 m, mixed with loud background noise |

**Recording protocol.**

1. At least 20 positive events per class and 2 hours of negatives (speech, music, tools, machinery, silence).
2. Mix noise at three levels (easy, loud, very loud) and note the level for each run.
3. Tune on the development set, then report on a held-out set recorded on a different day and in a different room.

**Metrics.** Recall per class against noise level, false alerts per hour, alarm-to-display latency (median and 95th percentile), and CPU use.

**Licensing.** Avoid CC-BY-NC clips in anything shown at a prize event, and credit every CC-BY clip.

## Prior art and positioning

Sound-to-sight on smart glasses has been researched, so Sentinel's claim is a deployable combination for hearing-protected workers, not a new concept. Say that plainly in the pitch.

| Prior work | What it does | Gap Sentinel fills |
|---|---|---|
| AR display of household sounds | Shows sound icons and spectrograms on smart glasses for deaf and hard-of-hearing people | Home use, not hazard alerts with latency targets |
| Smart glasses proximity warning | Visual alerts from Bluetooth beacons on equipment | Needs beacons fitted to vehicles. Sentinel listens to alarms already installed |
| AR warnings in work zones | Camera-based intrusion prediction with Vuzix glasses and a watch | Different sensing method, no hearing-protection focus |
| Construction auditory awareness review | Documents that hearing protection hurts hazard awareness and that research is lagging | Supports the problem statement |
| Hearing protector with siren alert (patent) | Alerts through the headset, noting visual alerts only work inside the visual field | Glasses keep the alert inside the visual field |
| Situational-awareness hearing protection | Passes useful sound through protection | Replaces the earmuff. Sentinel adds a visual channel and an event log |

What Sentinel adds: no retrofit to vehicles, works for workers in hearing protection and those with hearing loss, and a near-miss log that safety managers rarely have. Weaknesses to own: sound classifiers already run on phones, the display is small, and brightness is limited outdoors.

## Risk register

The biggest risk is that the SDK exposes less than we hope, so the plan puts every hardware dependency behind one adapter.

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SDK has no microphone access or limits what the display shows | High | High | Use an external mic and a text-only display path. Write the adapter interface now and build a simulator |
| Display too small or dim in bright light | Medium | High | Test on the real device the first night, use bold high-contrast text, blink for urgent alerts |
| Classifier misses real or speaker-played alarms | Medium | High | Tonal gate for pulsed alarms, calibrate thresholds on our own recordings, optional small fine-tune |
| Small speaker distorts the alarm sound | Medium | Medium | Use a bigger speaker, compare spectra against real clips, say how the demo sound was produced |
| Noisy venue, weak Wi-Fi | High | Medium | Fully offline pipeline, no cloud calls in the demo path, recorded backup video clearly labeled |
| False alerts from speech or music during the demo | Medium | High | Cooldowns, thresholds tuned on negatives, demo-mode toggle |
| Display link adds too much delay | Medium | Medium | Measure early, keep payloads tiny, pre-render alert text |
| Pre-build rule is questioned | Low | High | Keep written confirmation and disclose what was built beforehand |
| Overclaiming safety benefits | Medium | Medium | Use "assistive alert" wording and report only measured numbers |

## Pre-event schedule and event-day checklist

Plan: core software ready by Nov 19, glasses integration on Nov 20. Hacking starts Fri Nov 20 at 8:00 PM. Dates before it are targets for pre-built work; shift them once the SDK docs arrive.

| Item | Dates |
|---|---|
| Brief, questions sent | Nov 13 |
| Audio pipeline, clips | Nov 14 to Nov 15 |
| Gate and policy | Nov 16 to Nov 17 |
| Adapter, dashboard | Nov 17 to Nov 18 |
| Eval and backup video | Nov 18 to Nov 19 |
| Event integration | Nov 20 to Nov 21 |
| Code freeze, 2 PM | Nov 21 |

Event-day checklist, in order:

- [ ] Put text on the display and note how long an update takes.
- [ ] Decide L0 or L1 for audio and swap the input adapter.
- [ ] Re-measure alarm-to-display latency over the real display link.
- [ ] Test readability in bright light and lock the final alert style.
- [ ] Rerun the evaluation through the venue's microphone path.
- [ ] Freeze features by 1:00 PM Saturday, record the backup video, submit by 2:00 PM.

## Props and shopping list

Buy or borrow these by Nov 18 so the recording and demo rehearsals use the final gear. Prices are approximate and from memory, not sourced.

| Item | Why | Approx. cost |
|---|---|---|
| Earmuffs, noise-reduction rating around 25 dB | The "hearing-protected worker" demo moment | $15 to $25 |
| Bluetooth speaker with decent mid and low range | Plays the alarm clips behind the wearer | $30 to $60 |
| USB or lavalier microphone | External mic fallback if glasses audio is not exposed | $20 to $40 |
| Second matching mic | Only for the direction-finding stretch goal | $20 to $40 |
| Free sound-level meter app | Logs demo noise level for the test report | Free |
| Power strip and spare phone | Event-table basics and backup recording | $10 to $20 |
| Printed one-page summary | Leave-behind for judges | Pennies |

## Open decisions and sources

Five decisions are still open and each one changes the code plan.

- [ ] Team size and who owns glasses integration, audio pipeline, evaluation and dashboard.
- [ ] Language for the core: Python first with a C++ port of the hot path only if needed, or C++ from the start. Default for the coding agent: Python first.
- [ ] Processing host: laptop for the demo, or an Android phone if the SDK requires it. Default: laptop.
- [ ] Whether to fine-tune a small classifier head on our own clips or rely on the pretrained model plus the tonal gate. Default: no fine-tuning.
- [ ] Which sound plays in the demo: speaker-played recordings only, or a real alarm if the venue allows. Default: speaker-played recordings, labeled as such.

**Sources.** Found through search; most were read as search excerpts, not opened in full, and microlumin.net could not be fetched. Confirm key figures before quoting them publicly.

- Event: Worn on Luma, worn-hacks.com (site content did not load)
- Hardware: Tencent News on M6 series, Sohu M6 Display review, OFweek hands-on, Jiemian OpenDay report, MicroLumin
- Problem evidence: LHSFNA on backovers, Soundtrace on warehouse hearing loss, Sensear, Liftron
- Alarm specs: Federal Signal, Lawson, Brigade, ECCO
- Models and data: YAMNet TFLite, YAMNet class map, FSD50K
- Prior art: household sounds on AR glasses, proximity warning glasses, AR work-zone warnings, construction auditory awareness review, hearing protector patent

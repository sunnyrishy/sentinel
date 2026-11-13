# SDK notes: hardware facts as they arrive

**Status: nothing here is confirmed by MicroLumin.** The spec found no public MicroLumin SDK. Never invent SDK calls. When real facts arrive, paste them into the "Answers" sections below, and the code changes only in the slot listed for that fact.

## Facts seeded from public reviews (all UNCONFIRMED)

These come from Chinese-language press and reviews of the MLVISION M6 series (Tencent News, Sohu, OFweek, Jiemian). The spec rates them low to medium confidence. Replace each row with the confirmed answer.

| Fact | Source | Confidence | Confirmed? |
|---|---|---|---|
| The glasses pair with a phone app, and the app pushes content and sets brightness and screen-off time | OFweek review | Medium | UNCONFIRMED |
| The display module is a small monochrome green or full-color Micro-LED waveguide (JBD) | Tencent News | Medium | UNCONFIRMED |
| The image sits in the upper right of the view and is readable only from straight on | Sohu M6 Display review | Medium | UNCONFIRMED |
| The M6 Air base has a 12 MP camera (Sony IMX681) | Tencent News | Medium | UNCONFIRMED |
| Heavy AI work is expected to run on the phone or cloud | OFweek review | Medium | UNCONFIRMED |
| The glasses have audio (a microphone) | Jiemian | Low | UNCONFIRMED |
| Weaker in extreme light, battery compromises | Sohu review | Medium | UNCONFIRMED |
| Another vendor sells an unrelated "M6 AR Glass" on Android 10. Ignore its specs. | spec | None | n/a |

## Integration slots

Every unknown has a slot, a safe default and a place to record the answer.

| Unknown | Plugs into | Default until known | Answer recorded in |
|---|---|---|---|
| Display API: can it show text, icons, how fast can it update | `src/sentinel/adapters/display_microlumin.py` | Console and browser simulator | this file |
| Display size, characters per line, color | `display.caps` in `config/default.yaml` | 2 lines of 12 characters, monochrome green (assumed) | this file and the config |
| Glasses microphone access | `src/sentinel/adapters/audio_glasses.py` | Laptop or USB mic through `sounddevice` (`--input mic`) | this file |
| Connection path: phone app, BLE, Wi-Fi, direct to laptop | `src/sentinel/adapters/display_bridge.py` and the phone side script | WebSocket bridge | this file |
| Phone required or Android-only SDK | architecture choice below | Laptop core with a bridge | `docs/DECISIONS.md` |
| Update delay from our call to text on the lens | `tools/hw_smoke_test.py`, `tools/latency_probe.py` | Unmeasured | this file |
| Exact glasses model and display module | documentation only | M6 series assumed from public reviews | this file |

## Answers

Paste confirmed facts here. Quote the source (organizer message, SDK page, sample app) and the date.

### Model and display module
- Model teams get: _unknown_
- Display module (monochrome green or full color): _unknown_

### SDK form
- Android library, BLE protocol, cloud API or phone app only: _unknown_
- Link to docs or sample app: _unknown_

### Display
- Can it show arbitrary text and simple icons: _unknown_
- Maximum update rate: _unknown_
- Character limit and number of lines: _unknown_ (config currently assumes 2 lines of 12)
- Color: _unknown_ (config currently assumes `mono_green`)
- Native blink support: _unknown_ (config currently assumes none, so blinking is emulated with show and clear cycles)
- Brightness settable in code: _unknown_

### Audio
- Does our app receive the glasses microphone stream: _unknown_
- Format (raw PCM, sample rate, channels) and number of microphones: _unknown_
- Delivery mechanism (callback, socket, BLE, phone app forwarding): _unknown_

### Connectivity
- Phone required, or can a laptop connect directly: _unknown_
- Venue Wi-Fi reliable, own hotspot allowed: _unknown_

### Latency
- Typical delay from our app to text on the display: _unknown_

### Simulator or loaner unit
- Available before Nov 20: _unknown_

### Rules
- Pre-built code allowed (get this in writing): _unknown_
- Open-source libraries and pretrained models allowed: _unknown_
- Own mics and a speaker allowed: _unknown_
- Playing loud alarm sounds at our table during judging allowed: _unknown_

## Questions sent to the organizers

The spec lists these to send through the Luma event page (message the hosts), asking for a MicroLumin developer contact. Tick each when sent, and note the reply above.

- [ ] Which exact model do teams get: M6 Air plus which display module (monochrome green or full color)?
- [ ] What form is the SDK: Android library, BLE protocol, cloud API or phone app only? Can we see the docs or a sample app now?
- [ ] Display: can we send arbitrary text and simple icons? Maximum update rate, character limit, and can brightness be set in code?
- [ ] Audio: does our app receive the glasses' microphone stream (raw PCM, sample rate, channels)? How many mics are there?
- [ ] Connectivity: is a phone required, or can a laptop connect directly? Is Wi-Fi at the venue reliable, and can we use our own hotspot?
- [ ] Typical latency from our app to text appearing on the display?
- [ ] Is there a simulator or loaner unit so we can test before Nov 20?
- [ ] Rules: is pre-built code allowed, are open-source libraries and pretrained models allowed, and can we bring our own mics and a speaker?
- [ ] Demo: is playing loud alarm sounds at our table allowed during judging?

## Architecture decision

- **A, laptop core with a bridge (preferred, built).** The core runs on the laptop. `--display bridge` publishes the alert messages over WebSocket (`ws://HOST:PORT/ws`). A phone app or script subscribes and forwards each alert to the glasses through whatever the SDK offers. `tools/bridge_echo.py` is the reference subscriber: copy it and fill in `forward_to_glasses()`.
- **B, core on the phone.** Only if A cannot meet the latency target. It needs a port of the core. It is a decision for event day, not for the pre-build.

Decision so far: A. Record any change in `docs/DECISIONS.md`.

## How to try the bridge today (no hardware)

```bash
python tools/run_replay.py scenarios/tonal_10db.yaml --display bridge --realtime --no-classifier
python tools/bridge_echo.py          # in a second terminal
```

To reach a phone on the same network, set `dashboard.host: 0.0.0.0` in the config and subscribe to `ws://LAPTOP_IP:8765/ws`. There is no authentication, so only do this on a network you trust.

Message format (JSON text frames, from the spec):

```
{"type": "alert", "event_id": "e_000123", "line1": "BACKUP ALARM", "line2": "NEARBY", "tier": 1, "blink": true, "ttl_ms": 3000}
{"type": "clear", "event_id": "e_000123"}
{"type": "event", "event": {"id": "e_000123", "alert_class": "reversing_alarm", "confirmed": true}}
{"type": "stats", "events_total": 12, "p50_ms": 410, "p95_ms": 880}
```

A bridge needs only `alert` and `clear`. The text is already truncated to `display.caps`. When the display cannot blink, `blink` is false and the core sends show and clear cycles itself.

## Procedure when facts arrive

1. Paste the facts into "Answers" above, with the source and date.
2. **Display caps:** set `display.caps` in `config/default.yaml` (lines, `max_chars_per_line`, color, `supports_blink`).
3. **Display link:** if the SDK is a phone app or Android library, write the phone side of the bridge (start from `tools/bridge_echo.py`) and use `--display bridge`. If a laptop can talk to the glasses directly, implement `adapters/display_microlumin.py` following the TODO list at the top of that file.
4. **Smoke test the display:** `python tools/hw_smoke_test.py --display bridge` (or `microlumin`). It shows a fixed text, clears it, times 20 show and clear calls and prints the numbers. Paste them under "Measurements".
5. **Audio:** if the glasses microphone is readable, implement `adapters/audio_glasses.py` (TODO list at the top of that file) and try `python tools/run_live.py --input glasses`. Otherwise keep `--input mic` with an external microphone.
6. **End-to-end latency:** run `python tools/latency_probe.py --mode live --display bridge` and film the lens in slow motion as described in that tool's help. Paste the result under "Measurements".
7. Re-run the evaluation through the venue's microphone path (`docs/RECORDING_GUIDE.md`).

## Measurements

Paste numbers here as you get them. Say which adapter, which link and which day.

| Date | What | Result |
|---|---|---|
| | `hw_smoke_test` show p50 and p95 | |
| | `hw_smoke_test` clear p50 and p95 | |
| | `latency_probe` software alarm-to-display | |
| | Slow-motion video, sound onset to text on the lens | |

## Event-day checklist, in order

- [ ] Put text on the display and note how long an update takes.
- [ ] Decide L0 or L1 for audio and swap the input adapter (`--input glasses` or `--input mic`).
- [ ] Re-measure alarm-to-display latency over the real display link.
- [ ] Test readability in bright light and lock the final alert style.
- [ ] Rerun the evaluation through the venue's microphone path.
- [ ] Freeze features by 1:00 PM Saturday, record the backup video, submit by 2:00 PM.

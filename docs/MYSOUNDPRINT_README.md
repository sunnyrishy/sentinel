# MySoundprint

A personal sound filter for smart glasses. A microphone listens to the world, the software decides which sounds matter to this particular person right now, and the glasses show only those sounds, in two short lines of text.

We are building it for **Worn: Smart Glasses Hackathon** (Nov 20 to 21, 2026, City Tech, Brooklyn). This README is the single place that explains the event, the rules we are judged on, the idea, what already works, what does not, and what we plan to build. It is written so that a person or an AI assistant with no other context can read it and give useful advice.

If you are an AI assistant reading this, jump to "How to help us" at the bottom first.

---

## 1. The one paragraph version

Hearing protection and hearing loss hide the sounds that tell us what is happening around us: a doorbell, a siren, a smoke alarm, a car horn, a reversing truck. Every person cares about a different set of sounds, and the same sound matters differently at home, at work and when trying to sleep. MySoundprint listens, applies the wearer's own rules, and puts a short message on the lens only when something deserves an interruption. Everything else stays quiet, or waits for the wearer on their phone. All audio is processed on the device and none is stored.

It is an assistive awareness prototype. It is not a certified safety device and we never say it prevents accidents.

## 2. The event

| Item | Detail |
|---|---|
| Name | Worn: Smart Glasses Hackathon |
| Where | New York City College of Technology (City Tech), 285 Jay St, Brooklyn |
| When | Friday Nov 20 from 6:30 PM to Saturday Nov 21 6:00 PM |
| Hardware | MicroLumin M6 waveguide display glasses, handed to teams around 8:45 PM Friday |
| Teams | 4 to 5 people, mandatory, assigned on site at 8:00 PM Friday |
| Hacking | Starts 9:00 PM Friday (90 minutes with the hardware, out of the building by 10:30 PM), full build day Saturday |
| Code freeze | Saturday 2:00 PM, submissions due |
| Demo expo | Saturday 2:15 PM, judges walk from table to table, we demo on the glasses |
| Finals | Finalists announced 3:45 PM, then 3 minutes on stage worn by the team plus 2 minutes of questions |
| Prizes | 1st $1,500, 2nd $750, 3rd $500 |
| Event page | https://luma.com/smart-glass-hack-nyc and https://worn-hacks.com |

The organizers say the SDK and docs will be shared before the event. We had not received them when this was written. There is an SDK walkthrough at 7:35 PM Friday, SDK help during the first 90 minutes, and a hands-on SDK workshop Saturday at 10:00 AM.

### Theme and tracks

Theme: **Second nature.** Wearable AI that understands the person, works across their devices, and responds through the glasses so naturally it disappears into everyday life.

Every team picks one track:

1. **Personal Insight** (presented with Wardah): AI that understands the person. Privacy and consent must be built in from the start.
2. **Seamless Handoff:** experiences that move between the phone and the glasses, each device doing what it does best.
3. **Natural Interaction:** voice, touch and glanceable interfaces that feel so natural you forget you are using them.

We are going for **Natural Interaction**, with Seamless Handoff as the second story.

### How we are scored

Five equal criteria, each scored 0 (nonexistent), 1 (present but not prominent) or 2 (prominent). Total out of 10.

1. Understands you
2. Adaptability (the phone to glasses flow)
3. Ergonomics (less effort scores higher)
4. Works on the glasses
5. Impact and pitch

**The rule that can sink a team:** the project must run on the M6 glasses. A team averaging below 1 on "Works on the glasses" is not eligible for prizes, whatever else it scores. A phone demo with a simulator is not enough.

### Judges worth knowing about

Dr. Zhaoen Dai (founder and CEO of MicroLumin), Sam Shear (co-founder of Yes Hearing, a hearing care company), an XR and immersive experience designer, several hardware engineers and several venture investors. Accessibility for hard-of-hearing users is one of the listed challenge themes.

### What the event page does not tell us

Whether pre-built code is allowed, whether we may use our own microphone and speaker, whether alarm sounds are allowed at the table or on stage, the SDK form, the display size and update rate, and whether the glasses microphone is readable. These are open questions, listed in section 12.

## 3. What we know about the glasses (all unconfirmed)

None of this is confirmed by MicroLumin. It comes from press reviews of the MLVISION M6 series and is treated as a guess until the real docs arrive.

- Display: a small monochrome green (or possibly full color) Micro-LED waveguide, in the upper right of the view, readable mainly when looking straight at it. We assume about 2 lines of 12 characters. Brightness is limited in bright light.
- The M6 Air base has a 12 MP camera. Whether apps can reach the camera or the microphone is unknown.
- The glasses pair with a phone app. The likely path is a phone app or Android SDK pushing text to the display. Heavy processing is expected to run on the phone or in the cloud.

Because of this, the whole design treats the glasses as a **text display** and puts the sensing and the thinking somewhere else. We do not build anything that depends on the glasses camera, microphone or gestures until the SDK proves they exist.

## 4. The product

### What the wearer experiences

- A short alert appears on the lens only for sounds the wearer cares about, in the current situation. Two lines, at most 12 characters each, for example `DOORBELL` and `HOME`.
- The wearer never has to operate anything for this to happen.
- Sounds that do not matter right now are not shown. Some are saved for later and appear in a digest on the phone.
- The phone is the control and history side: choose which sounds matter, switch profiles, see what was missed, give feedback.

### The headline demo: same sound, three experiences

We play one doorbell sound three times.

| Profile | What happens |
|---|---|
| Focus | Nothing on the lens. The phone digest notes it for later. |
| Home | `DOORBELL` on the lens, the phone vibrates. |
| Sleep | Suppressed. A smoke alarm played afterwards still breaks through. |

This is what "understands you" looks like in ten seconds: the person and the situation change the outcome, not just the sound.

### Planned features, in build order

1. **Sound catalog.** A configured list of sounds, each with a glasses label of at most 12 characters per line and a default level. The classifier already knows all of these: doorbell, ding-dong, knock, siren, police and ambulance siren, bicycle bell, baby cry, smoke detector, fire alarm, microwave oven, alarm clock, vehicle horn, reversing beeps.
2. **Four levels per sound.** `interrupt` (lens, blink, phone vibration), `quiet` (lens, no blink), `later` (logged silently, shown in the phone digest), `off` (dropped).
3. **Named profiles.** Focus, Home, Sleep, Commute. Each maps every sound to a level and sets a sensitivity. One is active at a time.
4. **Context switch.** The wearer says "I am commuting" or "I am home" on the phone. A simulated place or time trigger is also available and is always labeled as simulated.
5. **Personal Sound DNA (experimental).** The wearer records about 5 seconds of their own sound (their microwave beep, their own chime), names it and picks a level. Matching uses a fingerprint of the sound. It only ships if it passes a real test (section 9).
6. **Feedback adaptation.** A "not useful" button on the phone. After three dismissals of the same sound in a profile, the phone suggests lowering its level. The wearer accepts or ignores it. Nothing changes by itself.
7. **Sound memory.** A history of what was detected, when, at what level and how the wearer responded, with a filter by sound. No audio is kept and there is no language understanding.
8. **Privacy screen.** Audio stored: 0 bytes. Network use: none. A plain explanation of every setting, and a consent step the first time the microphone starts.

### Deliberately left out

Sound pattern learning (beep sequences), a QR code profile swap, an analytics dashboard, name detection (needs speech recognition and has real privacy cost), direction finding, nod to acknowledge (only if the SDK exposes glasses motion), custom vibration patterns (optional if time allows).

### What we will and will not claim

- It adapts to the wearer in two real ways: they teach it their own sounds, and it suggests settings changes from their feedback. They approve every change. We do not say it "learns what sounds mean".
- We do not claim direction. The text never says "behind you". We have no direction finding.
- We cannot tell a doorbell on a television from a doorbell at the door. They sound the same.
- Every latency number is software only until measured on the glasses. Every accuracy number comes only from our own real recordings, never from synthetic audio.
- In the demo the microphone is a laptop or external USB microphone. A phone browser cannot stream its microphone to our laptop over plain http, so the core runs on the laptop for now. A production version would run on the phone. We say this out loud.

## 5. Where the code stands

This repository started as a project called **Sentinel**, a sound to sight alert system for hearing-protected workers (reversing alarms, horns, fire alarms). Sentinel is the engine. The Python package is still named `sentinel`. We keep that name in the code to avoid a risky rename right before the event.

### Built and tested (232 automated tests pass)

| Part | What it does |
|---|---|
| Audio pipeline | Microphone or wav file into a ring buffer. Threaded for live use, a deterministic virtual clock for tests. |
| Tonal gate | Pure signal processing that spots pulsed tonal alarms, with a first alert about 0.3 s after onset. |
| Sound classifier | YAMNet (TFLite, 521 sound classes), runs on the device, about 1 second window. |
| Alert policy | One alert on screen at a time, priorities, cooldowns, confirm or cancel, time to live. |
| Event log | SQLite, event metadata only, with latencies and an acknowledged flag. |
| Dashboard | FastAPI plus WebSocket, one static page, a glasses display simulator, kiosk mode, CSV export. |
| Phone page | Mobile page at `/phone`: the same alert with vibration, acknowledge, a simple profile (per sound on or off, sensitivity), a test alert button. |
| Bridge | The core publishes alerts over WebSocket so a phone app or script can forward them to the glasses. A reference subscriber is included. |
| Evaluation tools | Test set builder, evaluator with recall by class and noise level, false alerts per hour, latency, threshold sweeps. Every report states whether the audio was synthetic or real. |
| Demo mode | One command starts everything. If the microphone cannot be opened it falls back to a labeled replay with a visible RECORDING badge. |

### Not built yet

Everything in the planned feature list in section 4 beyond the simple profile that exists today. In particular: the wider sound catalog (today only three sounds plus smoke alarm are configured), levels, named profiles, the context switch, Sound DNA, feedback adaptation, the history view and the privacy screen.

### Blocked on the SDK

The real glasses display adapter and the glasses microphone adapter. Both exist only as stubs that raise `NotImplementedError` with a clear message. Nothing in this repo has ever run on the real glasses.

### Verified and not verified

- Verified on synthetic audio: gate behavior (including harmonics, clipping, reverb, pitch drift, band edges), policy rules, event log, dashboard, bridge, evaluation tooling, a full real-time run with the classifier at about 3 percent of one CPU core.
- Verified on real audio: one clip. The classifier loads offline and scored a cat meow as `Cat`. That is all.
- Not verified: the live microphone path on real hardware, recognition of real alarms, doorbells, sirens, anything on the glasses.
- Software latency on a synthetic backup alarm is about 0.3 s from sound onset to the display call returning. This is not a glasses number.

## 6. Architecture

```
microphone or wav
      |
 ring buffer
      |
      +--> tonal gate (fast, pulsed alarms)
      |
      +--> YAMNet classifier (what kind of sound)
              |
        alert policy  <--- active profile (levels, sensitivity)
              |
      +-------+----------------------+
      |                              |
 display adapter                event log (SQLite)
 console | simulator |                 |
 bridge | MicroLumin (stub)      dashboard and phone page
      |
 phone app or script  --->  glasses
```

Only two pieces touch hardware: the audio input adapter and the display adapter. Everything else runs and is tested on a laptop. The glasses are a text display behind the adapter, so swapping the simulator for the real lens should not touch detection code.

Integration architecture we plan to use: the core runs on the laptop and publishes alerts over WebSocket at `ws://HOST:8765/ws`. A phone app or script subscribes and forwards each alert to the glasses through whatever the SDK offers. A fallback is to move the core to the phone, which would need a port and is only a last resort.

Message format (JSON text frames):

```
{"type": "alert", "event_id": "e_000123", "line1": "BACKUP ALARM", "line2": "NEARBY", "tier": 1, "blink": true, "ttl_ms": 3000}
{"type": "clear", "event_id": "e_000123"}
{"type": "event", "event": {"id": "e_000123", "alert_class": "reversing_alarm", "confirmed": true}}
{"type": "stats", "events_total": 12, "p50_ms": 410, "p95_ms": 880}
```

## 7. Install and run

Python 3.11 or newer. On Linux the microphone needs PortAudio: `sudo apt install libportaudio2`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,classifier]"
python tools/fetch_models.py        # one time, needs network. After that everything runs offline.
```

Check the setup and run the tests:

```bash
python -m sentinel check-config
python -m sentinel check-models
pytest
ruff check .
```

Try it without any hardware:

```bash
python tools/run_replay.py scenarios/tonal_10db.yaml          # synthetic alarm through the pipeline
python tools/demo.py --mode replay                            # labeled replay with dashboard and kiosk page
```

With a microphone:

```bash
python tools/run_live.py --list-devices
python tools/run_live.py --sim                                # console alerts plus the browser simulator
python tools/demo.py                                          # full demo mode, opens the kiosk page
```

Phone page (a phone on the same network, no password, so use your own hotspot):

```bash
python tools/demo.py --lan
```

Open the printed `http://<laptop-ip>:8765/phone` on the phone. The dashboard is at `http://127.0.0.1:8765/`, and kiosk mode at `/?kiosk=1`.

Other tools: `tools/evaluate.py`, `tools/make_test_set.py`, `tools/latency_probe.py`, `tools/hw_smoke_test.py` and `tools/bridge_echo.py`. Each has `--help`.

## 8. Repository layout

```
config/default.yaml      every threshold and setting, validated on load
docs/
  MYSOUNDPRINT.md        locked project scope, gates, demo and team split
  EVENT_PLAN.md          schedule, scoring map, SDK walkthrough questions, stage script
  SDK_NOTES.md           what we know about the glasses, slots for the answers
  RECORDING_GUIDE.md     how to record a real test set
  DECISIONS.md           every design choice and why (D1 to D49)
  ONE_PAGER.md           one page summary for judges
  SPEC.md                the original engineering spec the engine was built from
src/sentinel/            types, config, buffer, gate, classifier, policy, event log, pipeline,
                         adapters (audio, display, bridge, stubs), dashboard, simulator, profile
tools/                   command line tools
scenarios/               synthetic test scenarios
tests/                   pytest suite
models/                  downloaded model files (not committed)
data/                    local clips and recordings (not committed)
```

Documents written before Nov 19 (`ONE_PAGER.md`, parts of `SPEC.md` and `RECORDING_GUIDE.md`) describe the Sentinel stage of the project. `MYSOUNDPRINT.md` is the current plan.

## 9. Gates: what must be true before we promise anything

1. **Glasses gate, Friday about 9:30 PM.** One real message on the real lens. If this fails, nothing else matters, because the baseline rule makes us ineligible. If the SDK blocks it, ask the hardware team for help right away and do not spend the night on the simulator.
2. **Catalog gate.** Play each catalog sound through a speaker at the microphone. Keep a sound in the demo only if it is detected in at least 8 of 10 plays and it does not false-alert during a 5 minute test with speech and music. Drop the rest.
3. **Sound DNA gate.** Teach it two different beeps. Keep the feature only if each is matched at least 8 of 10 times and the two are never confused with each other.
4. **Hardware stability.** Repeated updates replace old text, latency is acceptable, and the glasses do not disconnect.

## 10. The 3 minute stage demo

1. **Problem (20 s).** One person wears the glasses with headphones or earmuffs. The world is noisy and the sounds that matter are hidden.
2. **Same sound, three profiles (70 s).** The doorbell example from section 4, ending with a smoke alarm breaking through the Sleep profile.
3. **Teach a sound live (40 s).** Only if the Sound DNA gate passed. Record a chime, name it, set it to interrupt, play it, it shows on the lens.
4. **Feedback (25 s).** Dismiss an alert three times, accept the phone's suggestion.
5. **Privacy screen and history (15 s).** Zero bytes stored, no network.
6. **Limits and next steps (10 s).** Say what is prototype and what is not.

Backups: if the glasses fail on stage, the browser simulator, clearly labeled. If audio fails, `python tools/demo.py --mode replay`.

Likely judge questions and honest answers:

- Why not a phone notification? The phone needs a glance down, an unlock and a read. The glasses give a short cue without interrupting what the person is doing.
- How is it personal? The wearer chooses the level for each sound in each situation and can teach it their own sounds. The same sound gives different outcomes.
- What happens to the audio? It is processed on the device. No raw audio is stored by default and nothing goes to a cloud.
- How reliable is recognition? We measure it on our own recordings and report only those numbers. It covers a chosen set of sounds, not every sound.
- Is it a safety device? No.

## 11. Team and plan

Suggested roles for 4 to 5 people (teams are assigned on site, so adjust to who we get):

- **Glasses and SDK integration.** Display adapter, repeated updates, the test alert on the real lens. Friday 9:00 PM is theirs.
- **Sound engine.** Catalog, thresholds, real-sound testing, Sound DNA fingerprint.
- **Profiles and policy.** Levels, named profiles, context switch, feedback rules, event log changes.
- **Phone page.** Profiles, My Sounds, history, privacy screen, vibration.
- **Demo and pitch.** Sound library, rehearsal, one pager, stage script.

Schedule in short: before Friday we build the catalog, levels, profiles, context switch, feedback, history, privacy screen and a Sound DNA prototype with its test. All of it works without the SDK. At the event the team builds the glasses adapter, tunes on the venue microphone and rehearses. Feature freeze 1:00 PM Saturday, backup video recorded, submission by 2:00 PM.

Detail is in `docs/EVENT_PLAN.md` and `docs/MYSOUNDPRINT.md`.

## 12. Open questions

For the organizers, ideally asked in front of the room at the 7:35 PM walkthrough so the answers are on record:

1. How does text get onto the display, and from what: Android library, BLE, cloud API or the phone app only? Is there a sample app that shows text?
2. Can a laptop reach the glasses directly, or must everything go through a phone?
3. Characters per line, number of lines, update rate, color, brightness control, native blink?
4. Can our app read the glasses microphone? If not, is an external microphone acceptable as long as the alert appears on the lens?
5. Typical delay from our call to text on the lens?
6. Is pre-built code allowed, and are open-source libraries and pretrained models allowed?
7. May we play alarm sounds at the table and on stage?

For us:

- Real recordings of the catalog sounds (a phone recording of each is enough): doorbell, knock, siren, horn, smoke alarm, microwave beep. Without them the catalog gate cannot be run.
- Who is on the team, and whether they accept this idea at team formation.

## 13. Rules we work by

- Offline at runtime. No network calls once the model is downloaded.
- No raw audio stored unless the `--record` flag is given, and then only to `data/recordings/`.
- Report only measured results. Anything measured on synthetic audio is labeled synthetic and used for tests, never for claims.
- Use only CC0 or CC-BY audio from public sources and list every clip in `data/ATTRIBUTIONS.md`. Never commit audio files.
- No invented SDK details. Hardware specific code lives behind the two adapters.
- No em dashes in docs, comments or user interface text.
- Say what was built before the event, and get the pre-built code rule confirmed on record.

## 14. How to help us (for ChatGPT or any assistant reading this)

We are working with two assistants. Claude Code writes and tests the code in this repository. ChatGPT is used for ideas, critique and pitch work. Please do not repeat what is already decided here.

Useful things to do with this document:

1. **Challenge the scope.** Which planned features will not be understood in a 3 minute demo? What would you cut or sharpen?
2. **Improve "Understands you".** This is our weakest criterion. Suggest honest ways to show that the system adapts to a person, that we can build in about 18 hours without faking learning.
3. **Stress the demo.** Find the moments in the stage script that can fail and propose backups.
4. **Pitch work.** Tighten the 30 second team formation pitch and the stage script. Keep it plain and honest. Do not add claims we cannot back with measurements.
5. **Judge simulation.** Score us against the five criteria as a skeptical judge would, given only what is real today, and say what would move each score by one point.
6. **Risk review.** List what a rival team could do better and what a judge from the hearing care or hardware side would push on.

Ground rules for your answers: no em dashes, no invented numbers, mark anything that depends on the unknown SDK, and be critical rather than encouraging.

## 15. History

The project began as Sentinel, built against a written engineering spec, in nine milestones (scaffold, synthetic audio and tonal gate, live pipeline, classifier, policy and log, dashboard and simulator, evaluation, hardware slots, demo mode). On Nov 19 the organizers published the scoring rubric and theme, which led to the phone page, the profile, the test alert and the decision recorded as D49 in `docs/DECISIONS.md` to turn the engine into MySoundprint. This repository starts from that point.

## 16. License

Not chosen yet. Do not assume any license until one is added.

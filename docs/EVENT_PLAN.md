# Event plan: Worn, Nov 20 to 21

Facts below come from the official Luma page and the organizers' blast (Nov 19). Anything not on those is marked as unknown.

## What the organizers said that matters

- **Theme: "Second nature."** Wearable AI that understands the person, works across their devices, and responds through the glasses so naturally it disappears. Pick one track: Personal Insight, **Seamless Handoff** (phone and glasses, each doing what it does best), or **Natural Interaction** (voice, touch, glanceable interfaces).
- **Scoring: five equal criteria, 0 to 2 each, total 10:** Understands you; Adaptability (phone-to-glasses flow); Ergonomics (less effort scores higher); Works on the glasses; Impact and pitch.
- **Baseline: the project must run on the M6 glasses.** An average below 1 on "Works on the glasses" makes a team ineligible for prizes, whatever its other scores. Nothing else we build matters if text never appears on the lens.
- **Teams of 4 to 5 are mandatory and assigned during team formation** (Fri 8:00 PM). Entry is capped at 140.
- **Judges include Sam Shear, co-founder of Yes Hearing** (hearing care), and Dr. Dai (MicroLumin CEO). Accessibility for hard-of-hearing users is one of the listed challenge themes. Sentinel fits that audience directly.
- **Not stated on the page:** whether pre-built code is allowed, whether teams may use their own mics or speakers, whether loud alarm sounds are allowed at the table or on stage, what the SDK form is, and whether the glasses microphone is readable. The organizers say SDK docs are shared before the event. If you have not received them, ask the hosts today.

## Schedule and what to do in each slot

| When | Official item | Our move |
|---|---|---|
| Fri 6:30 PM | Doors, check-in | Laptop charged. Run `python tools/demo.py --mode replay` once to confirm everything works offline. Hotspot ready. |
| Fri 7:35 PM | Challenge brief and **SDK walkthrough** | Ask the questions below. Write answers straight into `docs/SDK_NOTES.md`. |
| Fri 8:00 PM | **Team formation** | Pitch Sentinel in 30 seconds (below). Say what exists already. |
| Fri 8:45 PM | Teams locked, hardware handed out | Get the glasses paired with a phone. |
| Fri 9:00 to 10:30 PM | 90 minutes with hardware and SDK help | **One goal: any text on the lens from the bridge.** Use the phone page "Send a test alert to the glasses" button for repeatable tests. Run `tools/hw_smoke_test.py`. |
| Sat 9:00 AM | Team check-in | Report status. Ask for SDK help on whatever blocks the display or mic. |
| Sat 10:00 AM | SDK and hardware workshop | Settle the audio question (glasses mic or external mic). |
| Sat 2:00 PM | **Code freeze, submissions due** | Freeze features by 1:00 PM. Record the backup video. |
| Sat 2:15 PM | Demo expo, judges at tables | Worn on the glasses. Kiosk page on the laptop. |
| Sat 4:00 PM | Finalists on stage, 3 minutes worn by the team, plus 2 minutes Q&A | Script below. |

## How Sentinel maps to the rubric (honest version)

| Criterion | What exists | Gap |
|---|---|---|
| Understands you | A per-person profile: which sounds matter and how sensitive it is, changed live from the phone page and applied immediately. Aimed at one specific person: a hearing-protected or hard-of-hearing worker. | It does not learn on its own. A reasonable event-day extension, only if time allows: suggest a sensitivity change from the share of alerts the wearer cancelled or acknowledged. Do not claim it until it exists. |
| Adaptability (phone to glasses) | Laptop core publishes alerts over WebSocket. The phone page shows the same alert with vibration, an acknowledge button, settings and a test button. The glasses are the glanceable layer, the phone is the control and history layer. | The phone page is a web page on the same network. If the SDK needs a native Android app, write a thin forwarder from `tools/bridge_echo.py`. |
| Ergonomics | Zero interaction needed: two words, auto-clear after 3 s, cooldown so it never nags. Acknowledging is optional. | None known. |
| Works on the glasses | Display adapter slots, bridge, smoke test, latency probe, all tested against the simulator. | **Blocked on the SDK. Not built.** This is the one that can make the team ineligible. |
| Impact and pitch | `docs/ONE_PAGER.md`, near-miss log and dashboard, honest limits. Judges include a hearing-care founder. | Real numbers from your own recordings are not in yet. Do not quote synthetic ones. |

Recommended track: **Natural Interaction** (a glanceable alert you never have to operate), with Seamless Handoff as the second story (phone page). This is your call.

## Questions for the 7:35 PM SDK walkthrough, in priority order

1. How does text get onto the display, and from what: Android library, BLE, cloud API or the phone app only? Is there a sample app that shows text?
2. Can a laptop reach the glasses directly, or must everything go through a phone?
3. Maximum characters per line, number of lines, update rate, color, can we set brightness, any native blink?
4. Can our app read the glasses' microphone (raw PCM, sample rate)? If not, is an external mic acceptable for "works on the glasses" as long as the alert shows on the lens?
5. Typical delay from our call to text on the lens?
6. Is pre-built code and open-source models allowed? (Ask in front of the room so the answer is on record.)
7. May we play alarm sounds at the table and on stage?

## What to bring

Laptop and charger, phone, an external USB or lavalier mic, a speaker, earmuffs, your own Wi-Fi hotspot (venue Wi-Fi may be unreliable), and a printed copy of `docs/ONE_PAGER.md`. The core runs fully offline, so only the phone-to-laptop link needs a network, and your hotspot covers it.

## 30-second pitch for team formation

"Sentinel turns the alarms workers can't hear into a two-word alert on the glasses. Earmuffs and hearing loss hide backup alarms and horns. The detection core, the phone companion view, the near-miss dashboard and a glasses simulator already run on a laptop and are tested. What we need on the team: someone to own the glasses SDK integration tonight, someone to record and evaluate real alarm audio, and someone to own the demo and pitch."

Suggested roles for 4 to 5 people: glasses and SDK integration; phone-side bridge and companion; real-audio recording and evaluation (`docs/RECORDING_GUIDE.md`); demo, kiosk and pitch; one floating person for whatever the SDK turns out to need.

Tell the team plainly what was built beforehand and show the commit history. Get the pre-built-code rule answered on record at the kickoff.

## 3-minute stage script (worn by the team)

1. **0:00** The problem in one sentence. Someone wearing earmuffs. A reversing alarm plays through the speaker (say it is a speaker-played recording).
2. **0:20** Text appears on the lens. Say the latency you measured on the glasses (only once measured).
3. **0:50** The phone in the other hand: same alert, vibration, tap to acknowledge. Flip a toggle (turn horns off) and show it applies at once.
4. **1:30** The dashboard: the near-miss log a safety manager never had. Latency and confirmed versus cancelled counts.
5. **2:15** Honest limits: assistive alert, not a certified safety device, classifier results measured on our own recordings (state the real numbers), direction unknown.
6. **2:45** What is built and what is next.

If anything fails, the labeled replay (`python tools/demo.py --mode replay`) is the backup, and the badge says RECORDING.

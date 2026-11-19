# MySoundprint: locked project scope for Worn

Decision date: Nov 19. This file replaces the idea brainstorm. Anything not listed here is out of scope.

## One sentence
MySoundprint is a personal sound attention layer. The phone listens and decides, per person and per situation, which sounds deserve an interruption, and the glasses show only those, in two short lines.

Pitch line: **"The world is noisy. Your glasses should know what matters to you."** Theme tie-in: second nature, because the wearer never operates it.

Track: Natural Interaction, with Seamless Handoff as the second story.

## What is true and what we may claim
- It adapts to the wearer in two real ways: the wearer **teaches it their own sounds**, and it **suggests setting changes from the wearer's own feedback**. The wearer approves every change.
- It does not "learn what sounds mean" on its own. Do not say that.
- Audio stays on the device. No raw audio stored. No cloud. Show this on a privacy screen.
- It is an assistive awareness prototype, not a certified safety device. Never claim direction ("BEHIND YOU"), because we have no direction finding.
- Latency numbers are software only until measured on the glasses. Recognition numbers come only from our own real recordings.
- Sensor in the demo is a **laptop microphone** (an external USB mic). Our core runs on the laptop and a phone browser cannot stream its mic over plain http on a LAN. Say so plainly: production would run on the phone.

## The feature set (build in this order)
1. **Sound catalog.** Widen from 3 sounds to a config-defined catalog. All of these are in the classifier already (class map index in brackets): Doorbell [349], Ding-dong [350], Knock [353], Siren [390], Police car or Ambulance siren [317, 318], Bicycle bell [198], Baby cry [20], Smoke detector [393], Fire alarm [394], Microwave oven [362], Alarm clock [389], Vehicle horn [302], Reversing beeps [313]. Each has a glasses label of at most 12 characters per line and a default level. This lifts the spec's three-class limit for this project (decision D49).
2. **Four levels per sound.** `interrupt` (glasses, blink, phone vibration), `quiet` (glasses, no blink), `later` (log silently, shown in the phone digest), `off` (dropped).
3. **Named profiles.** Focus, Home, Sleep, Commute. Each maps every sound to a level, plus a sensitivity. One active at a time, switched on the phone. This powers the headline demo: **same sound, three experiences.**
4. **Context switch (simulated, labeled).** A "I'm commuting" or "I'm home" trigger from the phone, and a clearly labeled simulated place or time trigger. Never presented as automatic detection.
5. **Personal Sound DNA (experimental).** The wearer records 5 seconds of their own sound, names it, and picks a level. Matching uses a fingerprint (average spectrum plus the classifier's score pattern) and cosine similarity. **Ship it only if it passes a real test** (see gates). The label is the wearer's own, so `FRONT DOOR` is their naming, not our claim.
6. **Feedback adaptation.** A "not useful" button on the phone, and unacknowledged alerts that repeat once. After 3 dismissals of the same sound in a profile, the phone suggests lowering its level. The wearer taps accept. Nothing changes by itself.
7. **Sound memory.** The event log (class, time, level, response, no audio) shown as a history on the phone, with a filter by sound ("did the washer beep?"). No language understanding.
8. **Privacy screen.** Audio stored: 0 bytes. Network: none. What each setting does. A consent step the first time the mic starts.

Cut for now: sound pattern learning, QR profile swap, analytics dashboard, name detection (needs speech), direction, nod to acknowledge (only if the SDK exposes glasses motion), custom haptic patterns (optional if time).

## Gates (test before promising)
- **Catalog gate:** play each catalog sound through a speaker at the laptop mic. Keep a sound only if it is detected in at least 8 of 10 plays and false-alerts on ordinary speech and music in a 5-minute test. Drop the rest from the demo.
- **Sound DNA gate:** teach two different beeps. Keep the feature only if each is matched at least 8 of 10 times and never confused with the other. A TV doorbell and a real doorbell are acoustically the same, so we do **not** claim to tell them apart.
- **Glasses gate (Friday, 9:30 PM):** one real message on the real lens. If this fails, nothing else matters.

## Demo (3 minutes, worn by the team)
1. Problem, 20 s: headphones on a New York street, or earmuffs, the world is noisy.
2. Same sound, three profiles, 70 s. Play a doorbell. Focus: nothing on the lens, phone digest shows it. Home: `DOORBELL / HOME`, phone vibrates. Sleep: suppressed, but a smoke alarm still breaks through.
3. Teach a sound live, 40 s (only if the DNA gate passed): record a chime, name it, set it to interrupt, play it, it appears.
4. Feedback, 25 s: dismiss an alert three times, accept the phone's suggestion.
5. Privacy screen and history, 15 s.
6. Honest limits and what is next, 10 s.

Backup if the glasses fail on stage: the browser simulator, labeled. Backup if audio fails: `python tools/demo.py --mode replay`.

## Team split (4 to 5 people)
- Glasses and SDK integration: display adapter, repeated updates, test alert on the real lens. Friday 9:00 PM is theirs.
- Sound engine: catalog, thresholds, real-sound testing, DNA fingerprint.
- Profiles and policy: levels, profiles, context switch, feedback rules, event log table.
- Phone page: profiles, My Sounds, history, privacy screen, vibration.
- Demo and pitch: sound library, rehearsal, one-pager, stage script.

## Before Friday (I build) and at the event (team builds)
Before: sound catalog, levels, named profiles, context trigger, feedback table, history view, privacy screen, DNA fingerprint prototype with its test. All offline, no SDK needed.
At the event: glasses adapter, real-sound tuning on the venue mic, demo rehearsal.

## Open items for the owner
- Provide real recordings of the catalog sounds (a phone recording of each is enough) so the gates can be run.
- Confirm with organizers: pre-built code allowed, own mic and speaker, alarms at table and on stage.

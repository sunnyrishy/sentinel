# Sentinel: one-page summary

**Sound-to-sight alerts for workers who cannot hear alarms.** Sentinel listens for reversing alarms, vehicle horns and fire alarms and shows a two-word alert on smart glasses, so it works through hearing protection and hearing loss. It is an assistive alert, not a certified safety device, and we do not claim it prevents accidents.

## The problem
Earplugs and earmuffs reduce all sound equally, hiding backup alarms and colleagues. Backup alarms are not required everywhere, and workers often cannot tell which direction a sound came from. Safety managers rarely have a record of near misses.

## What it does
- Detects a **pulsed backup alarm** within about 0.3 s with a fast signal-processing gate, and confirms the sound class with the YAMNet classifier (which also covers horns and fire alarms).
- Shows at most two words, `BACKUP ALARM` / `NEARBY`, blinks for urgent alerts, auto-clears after 3 s, with a cooldown so it never spams.
- Logs every event (time, class, confidence, latency, acknowledged) to a **near-miss dashboard** with a CSV export.
- **Runs fully offline.** No cloud, no raw audio stored. Only event metadata is kept.

## How it is built
One hardware-independent core and two thin adapters (audio in, display out). The glasses connect through a WebSocket bridge, so detection code never changes. A browser simulator of the glasses display lets the whole system run and be tested without the glasses.

## What is verified
| Claim | Status |
|---|---|
| Gate, policy, event log, dashboard, bridge, evaluation tooling | Tested (200+ automated tests) on synthetic audio |
| Gate handles harmonics, reverb, clipping, pitch drift, 60 to 80 pulses per minute | Tested on synthetic audio |
| Software latency to the display call (synthetic backup alarm) | About 0.3 s median. Software only, synthetic |
| Recall on real alarms at the demo noise level | **TO FILL IN** from the held-out evaluation |
| False alerts per hour in a noisy room | **TO FILL IN** from the held-out evaluation |
| Latency to visible photons on the glasses | **TO FILL IN** from slow-motion video |

Only numbers from our own held-out recordings, made through the event microphone path, are quoted on stage. Synthetic numbers are never presented as performance.

## Honest limits
- A pretrained classifier trained on YouTube audio: per-class results must be measured, and thresholds tuned on our recordings.
- Direction is unknown, so the alert never claims a direction (it says `NEARBY`).
- The display is small and brightness is limited outdoors.
- Sound classifiers already run on phones. Our value is the deployable combination for hearing-protected workers, the glasses channel and the near-miss log.

## Prior art
Sound-to-sight on glasses has been researched (household sounds for deaf users, beacon-based proximity warnings, camera-based work-zone warnings, hearing protectors with siren alerts). Sentinel needs no retrofit to vehicles: it listens to the alarms already installed.

## Built before the event
The core software (detection, policy, log, dashboard, simulator, evaluation tools, hardware adapter slots) was built before Nov 20 with the organizers' permission as we understood it. Glasses integration and real-recording evaluation happen at the event.

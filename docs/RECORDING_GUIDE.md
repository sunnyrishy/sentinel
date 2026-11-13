# Recording guide: build the real test set

Every number you show on stage must come from your own recordings, made through the same microphone path you use at the event. Public datasets and synthetic audio are only for development. This guide turns the spec's data and test plan into steps you can follow in an afternoon.

Sentinel is an assistive alert, not a certified safety device. Never say it prevents accidents.

## What you need to end up with

| Item | Minimum |
|---|---|
| Positive events per class | 20 each for `reversing_alarm`, `vehicle_horn`, `fire_alarm` |
| Negative audio | 2 hours total: speech, music, tools, machinery, silence |
| Noise levels | 3: easy, loud, very loud |
| Distances | 1 m, 3 m and 5 m from the speaker to the microphone |
| Sessions | A development set, and a held-out set recorded on a different day and in a different room |

You tune on the development set. You report only the held-out set. If you tune on the held-out set it stops being held out.

## Gear

- The speaker you will use in the demo, and the microphone path you will use at the event (laptop mic, USB or lavalier mic, or the glasses mic if the SDK exposes it). Using other gear makes the numbers meaningless for the demo.
- A free sound-level meter app on a phone. Put the phone next to the microphone and note the dB(A) of each noise level.
- Earmuffs. Play loud alarms at a sensible volume and wear hearing protection yourself.
- A way to cut audio into clips, for example Audacity (free).

## Alarm sources

You may play recordings of alarms through the speaker. Say so in the pitch: "speaker-played recordings, labeled as such".

- Your own recordings of real alarms (best). License: `own`.
- Freesound or FSD50K clips that are CC0 or CC-BY. Keep a credit for every CC-BY clip.
- Do not use CC-BY-NC clips in anything shown at a prize event. `make_test_set.py` skips them unless you pass `--allow-nc`, and then marks them development only.
- Reversing beeps come in two families. Record both a tonal alarm (a steady pitch around 1 to 3 kHz, pulsing about 60 to 80 times a minute) and, if you can find one, a broadband "white noise" alarm. The tonal gate only covers the first. The classifier covers the second.

## Step 1. Folder layout

```
data/clips/
  reversing_alarm/   one wav per event
  vehicle_horn/
  fire_alarm/
  negative/          long takes are fine, 5 to 20 minutes each
  manifest.csv       one row per file, created in step 4
```

`data/` is gitignored, so audio never gets committed. Name files so you can tell them apart, for example `day1_garage_3m_07.wav`.

## Step 2. Record the alarms

Do this once for the development session and once, on a different day and in a different room, for the held-out session.

1. Set up the speaker and microphone. Mark 1 m, 3 m and 5 m from the microphone on the floor.
2. Play each alarm class at each distance. Aim for at least 20 events per class per session, spread over the three distances. Vary the speaker angle a little between takes.
3. Each clip is one event: about 1 s of lead-in, then 3 to 10 s of alarm. For a pulsed backup alarm keep at least 3 pulses (about 4 s), because the gate confirms on the second pulse.
4. Record clean in a quiet room. The tool mixes in the noise levels for you (step 5), which keeps runs repeatable.
5. If you want a take with real background noise (a radio, a fan, machinery), record it separately and see "Takes that already contain noise" below.
6. Write the measured distance and anything unusual in the manifest notes column.

To capture audio through exactly the path Sentinel uses, run:

```bash
python tools/run_live.py --record --no-classifier
```

This writes the raw 16 kHz audio to `data/recordings/`. Cut the clips you want out of it and delete the rest. Recording is off by default and is the only way raw audio is ever stored.

## Step 3. Record the negatives

You need 2 hours in total of things that must not raise an alert. A missed alarm is bad, but false alerts during a demo are what judges remember.

- Speech: people talking near the microphone, a phone call on speaker.
- Music: at normal and loud volume.
- Tools and machinery: drill, vacuum, fan, kettle, door slams.
- Silence and room tone.
- Anything that sounds like an alarm but is not: phone notification tones, a microwave beep, a timer. These are the hard negatives.

Save them as 5 to 20 minute wav files in `data/clips/negative/`.

## Step 4. Licence manifest

Every clip needs a licence. Create the manifest rows and fill them in:

```bash
python tools/make_test_set.py --init-manifest
```

This adds a blank row for every clip in `data/clips/manifest.csv`. Open it in a spreadsheet and fill in:

| Column | Meaning |
|---|---|
| `file` | Already filled in. Path inside `data/clips/`. |
| `license` | `own`, `CC0` or `CC-BY`. Anything else is rejected. |
| `attribution` | Required for CC-BY: author and title. Optional otherwise. |
| `source_url` | Where a public clip came from. |
| `session` | A name for the recording session, for example `day1_garage` or `day2_office`. |
| `distance_m` | 1, 3 or 5 for alarm clips. |
| `notes` | Measured noise level in dB(A), speaker volume, anything odd. |

## Step 5. Build the scenarios

Pick the session that is your held-out set and run:

```bash
python tools/make_test_set.py --heldout-session day2_office
```

What it does:

- Reads the clips and the manifest. If any clip has no licence, or a licence that is not allowed, it lists every problem and stops.
- Puts every clip from the held-out session in the `heldout` split and everything else in `dev`.
- Mixes the clips into noise at three levels. By default `easy=10`, `loud=5` and `very_loud=0` are the signal-to-noise ratio in dB. Change them with `--levels easy=12,loud=6,very_loud=0` if your measured conditions are different.
- Leaves 10 s after each alarm so the 3 s alert time to live and the 5 s cooldown never merge two events.
- Writes scenario files to `scenarios/generated/`, a `manifest.json` with each clip's licence, session, split and checksum, and a table of the clips in `data/ATTRIBUTIONS.md`.

It also warns you if there is no held-out session. Without one, no number from this set may be reported.

### Takes that already contain noise

If a take was recorded with real noise playing, do not add more noise on top. Keep the recorded signal-to-noise ratio and add almost none:

```bash
python tools/make_test_set.py --noise-dbfs -70 --levels as_recorded=20 --prefix noisy
```

### Noise type

The added noise is pink by default. Use `--noise-type babble` for a speech-like background.

## Step 6. Tune on the development set

```bash
python tools/evaluate.py scenarios/generated/*.yaml --split dev --sweep-thresholds
```

The report in `reports/` shows precision and recall for each classifier threshold. Choose thresholds that give recall of at least 90% with as few false episodes as you can, then edit `classifier.classes.*.threshold` in `config/default.yaml`. Also check the gate settings (`gate.band_hz`, `gate.peak_to_median_db`) against your real reversing alarm: the 800 to 3000 Hz band and the 1.9 kHz pitch in the spec are assumptions.

If the smoke detector class (`Smoke detector, smoke alarm`) causes false alerts on your negatives, remove it from `fire_alarm.names`.

## Step 7. Report on the held-out set, once

```bash
python tools/evaluate.py scenarios/generated/*.yaml --split heldout
```

The report is labeled REAL and shows recall by class and SNR, false alerts per hour, latency percentiles and CPU use. Its goals table says "meets goal" or "below goal" for each requirement:

| ID | Goal |
|---|---|
| R1 | Recall at least 90% on your own clips at the demo noise level |
| R2 | Median alarm-to-display under 500 ms for tonal alarms, under 1.5 s for classifier only |
| R3 | Fewer than 1 false alert per 10 minutes in a noisy room |

Software latency is not end-to-end latency. Measuring the time to visible photons on the glasses needs hardware (slow-motion phone video). See `tools/latency_probe.py` once the display adapter exists.

## Rules for what you say on stage

- Quote only numbers from a REAL, held-out report, and say how many events they come from.
- Say the demo sounds were played through a speaker.
- Say what was built before the event.
- Never quote a synthetic number. Reports labeled SYNTHETIC say so at the top.

## Checklist

- [ ] 20 or more events per class in the development session
- [ ] 20 or more events per class in a held-out session on a different day and in a different room
- [ ] 2 hours of negatives
- [ ] Three noise levels, with the dB(A) of each written in the notes
- [ ] Distances 1, 3 and 5 m covered
- [ ] Every clip has a licence in `manifest.csv`, CC-BY clips have an attribution
- [ ] Thresholds tuned on `dev` only
- [ ] One held-out run, saved in `reports/`

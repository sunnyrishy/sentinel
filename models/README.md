# Models

Downloaded by `python tools/fetch_models.py`. The files themselves are gitignored. After the one-time download Sentinel makes no network calls.

| File | Source | SHA-256 |
|---|---|---|
| `yamnet_classification.tflite` | https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite | `4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf` |
| `yamnet_class_map.csv` | https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv | `cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2` |

## Facts verified from the downloaded model (Nov 13, 2026)

- Input: one tensor, shape `[15600]`, float32 waveform in -1..1 at 16 kHz. That is 0.975 s of audio, so the classifier cannot answer faster than about one window.
- Output: shape `[1, 521]`, per-class scores in 0..1. 521 rows in the class map, in output order.
- The configured names resolve exactly, by class map index: `Reversing beeps` 313, `Vehicle horn, car horn, honking` 302, `Air horn, truck horn` 312, `Fire alarm` 394, `Smoke detector, smoke alarm` 393.
- The class map also has `Alarm`, `Siren`, `Beep, bleep`, `Buzzer`. They are not used in v1.

## Provenance and licence

- The TFLite file is Google's MediaPipe-hosted YAMNet (float32). It is the classification variant with 521 classes, not the embedding-only variant.
- The class map is from the `tensorflow/models` repository (`research/audioset/yamnet`).
- Both come from Google repositories published under permissive licences. Confirm the licence text in the upstream repositories before redistributing them; this repo does not commit them.
- Runtime: `ai-edge-litert` (installed with `pip install -e ".[classifier]"`). Falls back to `tflite-runtime`, then `tensorflow`.

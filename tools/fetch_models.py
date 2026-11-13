"""Download the YAMNet TFLite classifier and its class map into models/ (needs network, one time).

After this, Sentinel runs fully offline. Files are checked against pinned SHA-256 checksums.
Usage: python tools/fetch_models.py [--force]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sentinel.config import resolve_path


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str


MODELS = [
    ModelFile(
        "yamnet_classification.tflite",
        "https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite",
        "4d8b4a53282dc83ef04e3e7dbc4fbc98082e34e44ed798e16c3a0cdd4c584faf",
    ),
    ModelFile(
        "yamnet_class_map.csv",
        "https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv",
        "cdf24d193e196d9e95912a2667051ae203e92a2ba09449218ccb40ef787c6df2",
    ),
]
EXPECTED_CLASSES = 521


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    tmp.replace(dest)


def verify_class_map(path: Path) -> int:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != EXPECTED_CLASSES:
        raise ValueError(f"class map has {len(rows)} rows, expected {EXPECTED_CLASSES}")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="download again even if present")
    args = parser.parse_args(argv)
    out_dir = resolve_path("models")
    out_dir.mkdir(exist_ok=True)
    for m in MODELS:
        dest = out_dir / m.name
        if dest.exists() and not args.force and sha256_of(dest) == m.sha256:
            print(f"ok      {m.name} (checksum matches)")
            continue
        print(f"fetch   {m.name}\n        {m.url}")
        try:
            download(m.url, dest)
        except OSError as err:
            print(
                f"Download failed: {err}\nFetch the file manually and place it at {dest}",
                file=sys.stderr,
            )
            return 1
        got = sha256_of(dest)
        if got != m.sha256:
            dest.unlink()
            print(
                f"Checksum mismatch for {m.name}\n expected {m.sha256}\n got      {got}",
                file=sys.stderr,
            )
            return 1
        print(f"        sha256 {got}")
    n = verify_class_map(out_dir / "yamnet_class_map.csv")
    print(f"Class map has {n} classes. Models are ready; Sentinel now runs offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

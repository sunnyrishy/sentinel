"""make_test_set: licences, splits, scenario generation, manifest, attributions."""

from __future__ import annotations

import importlib.util
import itertools
import json
from pathlib import Path

import pytest
import soundfile as sf
import yaml

from sentinel import testset
from sentinel.sim import synth
from sentinel.sim.scenario import load_scenario, render
from sentinel.types import SAMPLE_RATE

ROOT = Path(__file__).resolve().parents[1]


def write_clip(path: Path, kind: str = "alarm", seconds: float = 6.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "alarm":
        x = synth.tonal_alarm(1900, 70, seconds) * 0.6
    else:
        x = synth.noise("babble", seconds, seed=4, level_dbfs=-25)
    sf.write(str(path), x, SAMPLE_RATE, subtype="PCM_16")


def write_manifest(path: Path, rows: list[dict]) -> None:
    import csv

    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=testset.MANIFEST_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in testset.MANIFEST_COLUMNS})


@pytest.fixture
def clips(tmp_path: Path):
    d = tmp_path / "clips"
    rows = []
    for i, session in enumerate(["day1", "day1", "day2"]):
        rel = f"reversing_alarm/r{i}.wav"
        write_clip(d / rel)
        rows.append({"file": rel, "license": "own", "session": session})
    write_clip(d / "negative/speech.wav", "noise", 12)
    rows.append({"file": "negative/speech.wav", "license": "CC0", "session": "day1"})
    write_manifest(d / "manifest.csv", rows)
    return d


def test_normalize_license() -> None:
    n = testset.normalize_license
    assert n("CC0") == ("CC0", "ok") and n("cc0-1.0") == ("CC0", "ok")
    assert n("CC-BY 4.0") == ("CC-BY", "ok") and n("cc by") == ("CC-BY", "ok")
    assert n("Own recording") == ("own", "ok") and n("own") == ("own", "ok")
    assert n("CC-BY-NC 3.0")[1] == "nc"
    for bad in ["CC-BY-SA 4.0", "Sampling+", "all rights reserved", "CC-BY-ND"]:
        assert n(bad)[1] == "bad"


def test_init_manifest_adds_blank_rows_without_duplicates(tmp_path: Path) -> None:
    d = tmp_path / "clips"
    write_clip(d / "fire_alarm/a.wav")
    write_clip(d / "negative/b.wav", "noise")
    m = d / "manifest.csv"
    assert testset.write_skeleton(d, m) == 2
    assert testset.write_skeleton(d, m) == 0
    rows = testset.read_manifest(m)
    assert (
        set(rows) == {"fire_alarm/a.wav", "negative/b.wav"}
        and rows["fire_alarm/a.wav"]["license"] == ""
    )


def test_missing_license_lists_every_problem(tmp_path: Path) -> None:
    d = tmp_path / "clips"
    write_clip(d / "fire_alarm/a.wav")
    write_clip(d / "fire_alarm/b.wav")
    write_manifest(d / "manifest.csv", [{"file": "fire_alarm/a.wav", "license": "CC-BY-SA 4.0"}])
    with pytest.raises(testset.TestSetError) as exc:
        testset.collect_clips(d, testset.read_manifest(d / "manifest.csv"))
    msg = str(exc.value)
    assert "a.wav" in msg and "not allowed" in msg and "b.wav" in msg and "no licence" in msg


def test_cc_by_requires_attribution(tmp_path: Path) -> None:
    d = tmp_path / "clips"
    write_clip(d / "vehicle_horn/h.wav")
    write_manifest(d / "manifest.csv", [{"file": "vehicle_horn/h.wav", "license": "CC-BY"}])
    with pytest.raises(testset.TestSetError, match="attribution"):
        testset.collect_clips(d, testset.read_manifest(d / "manifest.csv"))


def test_nc_clips_skipped_unless_allowed(tmp_path: Path) -> None:
    d = tmp_path / "clips"
    write_clip(d / "vehicle_horn/h.wav")
    write_clip(d / "vehicle_horn/k.wav")
    write_manifest(
        d / "manifest.csv",
        [
            {"file": "vehicle_horn/h.wav", "license": "CC-BY-NC", "attribution": "x"},
            {"file": "vehicle_horn/k.wav", "license": "CC0"},
        ],
    )
    man = testset.read_manifest(d / "manifest.csv")
    clips, warns = testset.collect_clips(d, man)
    assert [c.rel for c in clips] == ["vehicle_horn/k.wav"] and any("skipped" in w for w in warns)
    clips, warns = testset.collect_clips(d, man, allow_nc=True)
    assert len(clips) == 2 and any("prize event" in w for w in warns)
    assert next(c for c in clips if c.rel.endswith("h.wav")).dev_only


def test_heldout_split_follows_session_and_warns_without_one(clips: Path) -> None:
    man = testset.read_manifest(clips / "manifest.csv")
    got, warns = testset.collect_clips(clips, man, heldout_session="day2")
    assert {c.rel: c.split for c in got}["reversing_alarm/r2.wav"] == "heldout"
    assert sum(c.split == "dev" for c in got) == 3 and not any(
        "no --heldout-session" in w for w in warns
    )
    _, warns = testset.collect_clips(clips, man)
    assert any("no --heldout-session" in w for w in warns)


def test_no_clips_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(testset.TestSetError, match="No clips found"):
        testset.collect_clips(tmp_path, {})


def test_parse_levels() -> None:
    assert testset.parse_levels("easy=10,loud=5") == [("easy", 10.0), ("loud", 5.0)]
    with pytest.raises(testset.TestSetError, match="bad --levels"):
        testset.parse_levels("easy")


def test_build_scenarios_structure(clips: Path) -> None:
    got, _ = testset.collect_clips(clips, testset.read_manifest(clips / "manifest.csv"), "day2")
    scs = testset.build_scenarios(got, testset.DEFAULT_LEVELS, gap_s=10.0)
    names = [s["name"] for s in scs]
    assert "testset_dev_easy_pos_01" in names and "testset_heldout_very_loud_pos_01" in names
    assert "testset_dev_loud_neg_01" in names and "testset_dev_easy_noise_only" in names
    assert not any("heldout" in n and "neg" in n for n in names)  # the negative clip is a day1 one
    pos = next(s for s in scs if s["name"] == "testset_dev_easy_pos_01")
    starts = [e["start_s"] for e in pos["events"]]
    assert starts[0] == 3.0 and all(
        b - a >= 6.0 + 10.0 - 1e-6 for a, b in itertools.pairwise(starts)
    )
    assert {e["snr_db"] for e in pos["events"]} == {10.0}
    neg = next(s for s in scs if s["name"] == "testset_dev_loud_neg_01")
    assert all(e["label"] is None and e["snr_db"] == 5.0 for e in neg["events"])
    assert all(s["origin"] == "real" for s in scs)


def test_long_sets_are_split_into_several_scenarios(clips: Path) -> None:
    got, _ = testset.collect_clips(clips, testset.read_manifest(clips / "manifest.csv"))
    scs = testset.build_scenarios(got, [("easy", 10.0)], gap_s=10.0, max_scenario_s=20.0)
    assert len([s for s in scs if "_pos_" in s["name"]]) >= 2
    assert all(s["duration_s"] <= 20.0 + 6.0 + 10.0 + 5 for s in scs if "_pos_" in s["name"])


def test_tool_end_to_end_then_evaluate(clips: Path, tmp_path: Path, capsys) -> None:
    spec = importlib.util.spec_from_file_location("mts", ROOT / "tools" / "make_test_set.py")
    mts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mts)
    out = tmp_path / "gen"
    attr = tmp_path / "ATTRIBUTIONS.md"
    args = [
        "--clips",
        str(clips),
        "--out",
        str(out),
        "--heldout-session",
        "day2",
        "--noise-only-s",
        "60",
        "--attributions",
        str(attr),
    ]
    assert mts.main(args) == 0
    assert mts.main(args) == 0  # idempotent
    printed = capsys.readouterr().out
    assert "heldout: 1 clips" in printed and "scenario files" in printed
    # manifest
    man = json.loads((out / "manifest.json").read_text())
    clip = next(c for c in man["clips"] if c["file"] == "reversing_alarm/r2.wav")
    assert clip["split"] == "heldout" and clip["license"] == "own" and len(clip["sha256"]) == 64
    assert man["parameters"]["heldout_session"] == "day2"
    # attributions block exists once
    text = attr.read_text()
    assert text.count(testset.BEGIN_MARK) == 1 and "negative/speech.wav" in text and "CC0" in text
    # scenarios load and render with the real origin and split
    heldout = sorted(out.glob("*heldout_easy_pos*.yaml"))[0]
    sc = load_scenario(heldout)
    assert sc.origin == "real" and sc.split == "heldout" and sc.level == "easy"
    rendered = render(sc)
    assert rendered.truth[0].label == "reversing_alarm" and rendered.truth[0].start_s >= 3.0
    # the evaluator reads them and labels the report REAL
    ev_spec = importlib.util.spec_from_file_location("evaluate", ROOT / "tools" / "evaluate.py")
    ev = importlib.util.module_from_spec(ev_spec)
    ev_spec.loader.exec_module(ev)
    rep = tmp_path / "reports"
    files = [str(p) for p in out.glob("*.yaml")]
    assert (
        ev.main([*files, "--split", "heldout", "--no-classifier", "--out", str(rep), "--name", "r"])
        == 0
    )
    report = (rep / "r_real.md").read_text()
    assert "REAL RECORDINGS" in report and "heldout" in report and "SYNTHETIC AUDIO" not in report
    assert "reversing_alarm" in report


def test_scenario_with_clip_requires_origin(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "x",
                "duration_s": 10,
                "noise": {"type": "pink"},
                "events": [{"kind": "clip", "path": "a.wav", "start_s": 1}],
            }
        )
    )
    from sentinel.config import ConfigError

    with pytest.raises(ConfigError, match="origin"):
        load_scenario(p)


def test_gap_default_exceeds_ttl_plus_cooldown() -> None:
    cfg = __import__("sentinel.config", fromlist=["load_config"]).load_config()
    assert cfg.policy.ttl_s + cfg.policy.cooldown_s < 10.0

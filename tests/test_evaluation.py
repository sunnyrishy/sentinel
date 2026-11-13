"""Scoring rules, aggregation, sweep, report labelling and the evaluate tool."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from sentinel.config import load_config
from sentinel.evaluation import (
    AlertRecord,
    NullDisplay,
    ScenarioResult,
    SweepItem,
    TruthResult,
    aggregate,
    default_template,
    render_report,
    run_scenario,
    score_scenario,
    smoothed_flags,
    sweep_thresholds,
    synthetic_suite,
    window_scores,
)
from sentinel.sim.scenario import RenderedScenario, Scenario, render
from sentinel.sim.scenario import TruthEvent as SceneTruth
from sentinel.types import SAMPLE_RATE, AlertClass, Detection

CFG = load_config()
ROOT = Path(__file__).resolve().parents[1]
R, H = AlertClass.REVERSING_ALARM, AlertClass.VEHICLE_HORN


def rendered(truth: list[SceneTruth], duration_s: float = 60.0, origin: str = "synthetic"):
    audio = np.zeros(int(duration_s * SAMPLE_RATE), dtype=np.float32)
    return RenderedScenario("t", audio, truth, origin, "pink", -30.0)


def det(cls: AlertClass, t: float, provisional: bool = False) -> Detection:
    return Detection(cls, "gate", 0.9, provisional, t_onset=t - 0.3, t_detect=t)


def row(
    eid,
    cls,
    t_detect,
    *,
    confirmed=True,
    cancel=None,
    shown=True,
    stage="gate",
    t_onset=None,
    display_delay=0.01,
):
    t_onset = t_detect - 0.3 if t_onset is None else t_onset
    return {
        "id": eid,
        "alert_class": cls,
        "stage": stage,
        "confirmed": int(confirmed),
        "cancel_reason": cancel,
        "t_onset": t_onset,
        "t_detect": t_detect,
        "t_display": t_detect + display_delay if shown else None,
        "processing_ms": display_delay * 1000,
        "alarm_to_display_ms": (t_detect + display_delay - t_onset) * 1000,
    }


EVENT = SceneTruth("reversing_alarm", "tonal_alarm", 10.0, 16.0, 10.0)


# ---- scoring ----
def test_detected_when_confirmed_detection_inside_window() -> None:
    truth, _ = score_scenario(
        rendered([EVENT]), [det(R, 11.2)], [row("e1", "reversing_alarm", 10.3)]
    )
    assert truth[0].detected


@pytest.mark.parametrize("t", [9.99, 17.01])
def test_detection_outside_start_to_end_plus_1s_does_not_count(t: float) -> None:
    truth, _ = score_scenario(rendered([EVENT]), [det(R, t)], [])
    assert not truth[0].detected


def test_detection_at_end_plus_one_second_counts() -> None:
    truth, _ = score_scenario(rendered([EVENT]), [det(R, 17.0)], [])
    assert truth[0].detected


def test_provisional_and_wrong_class_detections_do_not_count() -> None:
    truth, _ = score_scenario(rendered([EVENT]), [det(R, 11.0, provisional=True), det(H, 11.0)], [])
    assert not truth[0].detected


def test_false_alerts_split_confirmed_and_cancelled() -> None:
    rows = [
        row("e1", "reversing_alarm", 10.4),  # matches the event
        row("e2", "reversing_alarm", 30.0, confirmed=True),  # confirmed false alert
        row("e3", "reversing_alarm", 40.0, confirmed=False, cancel="timeout"),  # cancelled
        row("e4", "vehicle_horn", 12.0),  # right time, wrong class: false
    ]
    _, alerts = score_scenario(rendered([EVENT]), [], rows)
    by = {a.event_id: a for a in alerts}
    assert (
        by["e1"].matched and not by["e2"].matched and not by["e3"].matched and not by["e4"].matched
    )
    agg = aggregate([ScenarioResult("t", "synthetic", None, 3600.0, "pink", -30.0, [], alerts)])
    assert (agg.neg_confirmed_false, agg.neg_cancelled_false) == (
        2,
        1,
    )  # no labelled truth: negative-only


def test_alert_never_shown_is_not_a_false_alert() -> None:
    rows = [row("e1", "vehicle_horn", 30.0, shown=False, cancel="superseded")]
    _, alerts = score_scenario(rendered([]), [], rows)
    assert alerts == []


def test_decoy_events_are_not_scored_and_alerts_near_them_are_false() -> None:
    decoy = SceneTruth(None, "beep", 10.0, 10.1, 10.0)
    truth, alerts = score_scenario(rendered([decoy]), [], [row("e1", "reversing_alarm", 10.2)])
    assert not truth[0].detected and not alerts[0].matched


def test_latency_uses_ground_truth_onset_and_first_alert() -> None:
    rows = [row("e1", "reversing_alarm", 10.30, display_delay=0.002, t_onset=9.95)]
    truth, _ = score_scenario(rendered([EVENT]), [det(R, 11.2)], rows)
    t = truth[0]
    assert t.stage == "gate"
    assert t.a2d_truth_ms == pytest.approx(302.0, abs=0.1)  # 10.302 - 10.000 truth onset
    assert t.a2d_est_ms == pytest.approx(352.0, abs=0.1)  # 10.302 - 9.95 estimated onset
    assert t.processing_ms == pytest.approx(2.0)


# ---- aggregation ----
def result(name, truth, alerts=(), hours=1.0, origin="synthetic", level=None, split=None):
    return ScenarioResult(
        name, origin, split, hours * 3600, "pink", -30.0, list(truth), list(alerts), 0.0, level
    )


def test_recall_tables_by_class_snr_and_noise() -> None:
    hit = TruthResult("reversing_alarm", "tonal_alarm", 1, 2, 10.0, detected=True)
    miss = TruthResult("reversing_alarm", "tonal_alarm", 3, 4, 10.0, detected=False)
    low = TruthResult("reversing_alarm", "tonal_alarm", 5, 6, 0.0, detected=False)
    agg = aggregate([result("a", [hit, miss, low], level="loud")])
    assert agg.recall_by_snr[("reversing_alarm", 10.0)] == (1, 2)
    assert agg.recall_by_snr[("reversing_alarm", 0.0)] == (0, 1)
    assert agg.recall_by_class["reversing_alarm"] == (1, 3)
    assert agg.recall_by_noise[("reversing_alarm", "loud, pink -30 dBFS")] == (1, 3)


def test_false_alerts_per_hour_only_from_negative_scenarios() -> None:
    def a(eid: str, conf: bool) -> AlertRecord:
        return AlertRecord(eid, "fire_alarm", "classifier", conf, None if conf else "timeout", 5.0)

    neg = result("neg", [], [a("e1", True), a("e2", False)], hours=2.0)
    pos = result(
        "pos",
        [TruthResult("fire_alarm", "clip", 1, 2, 5.0, detected=True)],
        [a("e3", True)],
        hours=1.0,
    )
    agg = aggregate([neg, pos])
    assert (
        agg.neg_hours == pytest.approx(2.0)
        and agg.neg_confirmed_false == 1
        and agg.neg_cancelled_false == 1
    )
    assert agg.pos_hours == pytest.approx(1.0) and agg.pos_confirmed_false == 1


def test_latency_groups_split_tonal_and_classifier() -> None:
    g = TruthResult("reversing_alarm", "x", 1, 2, 5, True, "gate", 1.0, 300.0, 350.0)
    both = TruthResult("reversing_alarm", "x", 3, 4, 5, True, "both", 1.0, 320.0, 360.0)
    c = TruthResult("vehicle_horn", "x", 5, 6, 5, True, "classifier", 4.0, 900.0, 800.0)
    agg = aggregate([result("a", [g, both, c])])
    assert agg.latency["tonal"]["truth"] == [300.0, 320.0]
    assert agg.latency["classifier"]["truth"] == [900.0]


# ---- threshold sweep ----
def test_smoothed_flags_two_of_three() -> None:
    s = np.array([0.0, 0.9, 0.0, 0.9, 0.9, 0.0, 0.0, 0.0])
    assert smoothed_flags(s, 0.5, 2, 3).tolist() == [
        False,
        False,
        False,
        True,
        True,
        True,
        False,
        False,
    ]


def test_sweep_precision_and_recall_change_with_threshold() -> None:
    times = np.arange(1.0, 40.0, 0.25)
    scores = np.zeros(len(times))
    scores[(times >= 10) & (times <= 12)] = 0.8  # during the labelled event
    scores[(times >= 30) & (times <= 31)] = 0.4  # a weaker false episode
    truth = [TruthResult("vehicle_horn", "clip", 10.0, 12.0, 5.0)]
    item = SweepItem(truth, 3600.0, times, {H: scores})
    rows = {r.threshold: r for r in sweep_thresholds([item], 2, 3, [0.3, 0.5, 0.9])}
    assert rows[0.3].recall == 1.0 and rows[0.3].precision == 0.5 and rows[0.3].fp_episodes == 1
    assert rows[0.5].recall == 1.0 and rows[0.5].precision == 1.0 and rows[0.5].fp_per_hour == 0.0
    assert rows[0.9].recall == 0.0 and rows[0.9].precision is None


def test_window_scores_cadence_and_values() -> None:
    class Fake:
        window_len = 8000
        class_indices = {H: [0]}

        def infer(self, w):
            out = np.zeros(521, dtype=np.float32)
            out[0] = float(np.max(np.abs(w)))
            return out

    audio = np.zeros(SAMPLE_RATE * 3, dtype=np.float32)
    audio[SAMPLE_RATE * 2 :] = 0.7
    times, per = window_scores(audio, Fake(), hop_s=0.5)
    assert times[0] == pytest.approx(0.5) and times[1] - times[0] == pytest.approx(0.5)
    assert per[H][0] == 0.0 and per[H][-1] == pytest.approx(0.7)


# ---- end to end runs ----
def test_run_scenario_detects_synthetic_alarm_and_negatives_are_clean() -> None:
    pos = render(
        Scenario.model_validate(
            {
                "name": "p",
                "duration_s": 30,
                "seed": 1,
                "noise": {"type": "pink", "level_dbfs": -30},
                "events": [
                    {
                        "kind": "tonal_alarm",
                        "start_s": 10,
                        "duration_s": 6,
                        "snr_db": 10,
                        "label": "reversing_alarm",
                    }
                ],
            }
        )
    )
    r = run_scenario(pos, CFG)
    assert r.truth[0].detected and r.truth[0].stage == "gate"
    assert 200 < r.truth[0].a2d_truth_ms < 600
    assert all(a.matched for a in r.alerts) and r.cpu_s > 0
    neg = render(
        Scenario.model_validate(
            {"name": "n", "duration_s": 60, "seed": 2, "noise": {"type": "pink", "level_dbfs": -30}}
        )
    )
    assert run_scenario(neg, CFG).alerts == []


def test_synthetic_suite_shapes() -> None:
    quick = synthetic_suite(quick=True)
    full = synthetic_suite()
    assert 3 <= len(quick) < len(full)
    assert any(s.name == "syn_negative_decoys" for s in full)
    assert all(s.origin is None for s in full)  # generated, so reports label them synthetic


# ---- report labelling ----
def make_results(origin: str) -> list[ScenarioResult]:
    hit = TruthResult("reversing_alarm", "tonal_alarm", 1, 2, 10.0, True, "gate", 1.0, 300.0, 350.0)
    return [
        result(
            "a", [hit], [], hours=0.5, origin=origin, split="heldout" if origin == "real" else None
        )
    ]


def test_synthetic_report_is_labelled_and_makes_no_claims() -> None:
    text = render_report(default_template(), "synthetic", make_results("synthetic"), CFG)
    assert "SYNTHETIC AUDIO" in text and "Do not quote them as performance" in text
    assert "n/a (synthetic, not a claim)" in text
    assert "meets goal" not in text and "below goal" not in text
    assert "Config hash" in text and CFG.config_hash() in text


def test_real_report_is_labelled_and_states_goal_status() -> None:
    text = render_report(default_template(), "real", make_results("real"), CFG)
    assert "REAL RECORDINGS" in text and "heldout" in text and "SYNTHETIC AUDIO" not in text
    assert "meets goal" in text  # 1/1 recall and 300 ms median
    assert "not a claim" not in text


def test_report_notes_software_only_latency_and_disabled_classifier() -> None:
    text = render_report(
        default_template(), "synthetic", make_results("synthetic"), CFG, classifier_used=False
    )
    assert "software only" in text and "classifier was disabled" in text


def test_report_has_no_em_dash() -> None:
    assert chr(0x2014) not in render_report(
        default_template(), "synthetic", make_results("synthetic"), CFG
    )


def test_null_display_is_a_valid_display() -> None:
    d = NullDisplay()
    d.show(None)  # type: ignore[arg-type]
    d.clear()
    d.close()
    assert d.capabilities().supports_blink


# ---- the tool ----
def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_evaluate_tool_writes_labelled_report(tmp_path: Path, capsys) -> None:
    tool = load_tool("evaluate")
    rc = tool.main(
        [
            "--synthetic-suite",
            "--quick",
            "--no-classifier",
            "--out",
            str(tmp_path / "rep"),
            "--name",
            "t",
        ]
    )
    assert rc == 0
    files = list((tmp_path / "rep").glob("t_synthetic.md"))
    assert len(files) == 1
    text = files[0].read_text()
    assert "SYNTHETIC AUDIO" in text and "reversing_alarm" in text
    assert "False alerts per hour" in text and "Alarm to display p50" in text
    assert "Wrote" in capsys.readouterr().out


def test_evaluate_tool_reports_missing_scenarios(tmp_path: Path, capsys) -> None:
    tool = load_tool("evaluate")
    assert tool.main([str(tmp_path / "nope.yaml"), "--no-classifier"]) == 2
    assert "not found" in capsys.readouterr().err


def test_evaluate_tool_sweep_needs_classifier_and_prints_table(tmp_path: Path) -> None:
    clf = ROOT / "models" / "yamnet_classification.tflite"
    if not clf.exists():
        pytest.skip("run tools/fetch_models.py first")
    tool = load_tool("evaluate")
    rc = tool.main(
        [
            "--synthetic-suite",
            "--quick",
            "--sweep-thresholds",
            "--thresholds",
            "0.2:0.6:0.2",
            "--out",
            str(tmp_path),
            "--name",
            "s",
        ]
    )
    assert rc == 0
    text = (tmp_path / "s_synthetic.md").read_text()
    assert "Precision" in text and "vehicle_horn" in text and "0.20" in text

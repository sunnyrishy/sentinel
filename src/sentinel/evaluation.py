"""Scoring and reporting for tools/evaluate.py.

Scoring rules (spec, Synthetic data and test harness):
  * A labelled event is DETECTED if a confirmed (non-provisional) detection of the right class
    occurs between its start and its end plus 1.0 s.
  * A FALSE ALERT is any alert that was shown with no matching event, whether or not it was later
    confirmed. Confirmed false alerts and cancelled provisional alerts are reported separately.
Every report says whether the audio was synthetic or real.
"""

from __future__ import annotations

import string
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from sentinel.adapters.audio_file import AudioFileSource
from sentinel.classify.yamnet import Classifier, ClassifierStage, class_scores
from sentinel.config import PROJECT_ROOT, SentinelConfig
from sentinel.eventlog import EventLog
from sentinel.metrics import percentile
from sentinel.pipeline import Pipeline
from sentinel.sim.scenario import RenderedScenario, Scenario, render
from sentinel.types import SAMPLE_RATE, Alert, AlertClass, Detection, DisplayCaps

MATCH_TAIL_S = 1.0  # an event may be detected up to this long after it ends
TARGET_RECALL = 0.90  # R1
TARGET_TONAL_MEDIAN_MS = 500.0  # R2
TARGET_CLASSIFIER_MEDIAN_MS = 1500.0  # R2
TARGET_FALSE_ALERTS_PER_HOUR = 6.0  # R3: under 1 per 10 minutes


class NullDisplay:
    """Evaluation display: accepts everything, draws nothing."""

    def capabilities(self) -> DisplayCaps:
        return DisplayCaps(supports_blink=True)

    def show(self, alert: Alert) -> None:
        pass

    def clear(self, event_id: str | None = None) -> None:
        pass

    def close(self) -> None:
        pass


@dataclass
class TruthResult:
    label: str | None
    kind: str
    start_s: float
    end_s: float
    snr_db: float
    detected: bool = False
    stage: str | None = None  # stage of the alert that first showed for this event
    processing_ms: float | None = None
    a2d_truth_ms: float | None = None  # first display minus ground-truth onset
    a2d_est_ms: float | None = None  # first display minus the detector's estimated onset


@dataclass
class AlertRecord:
    event_id: str
    alert_class: str
    stage: str
    confirmed: bool
    cancel_reason: str | None
    t_detect: float
    matched: bool = False


@dataclass
class ScenarioResult:
    name: str
    origin: str
    split: str | None
    duration_s: float
    noise_type: str
    noise_dbfs: float
    truth: list[TruthResult]
    alerts: list[AlertRecord]
    cpu_s: float = 0.0
    level: str | None = None

    @property
    def negative_only(self) -> bool:
        return not any(t.label for t in self.truth)


# ---- scoring ----
def score_scenario(
    rendered: RenderedScenario, detections: list[Detection], rows: list[dict[str, Any]]
) -> tuple[list[TruthResult], list[AlertRecord]]:
    """Apply the spec's scoring rules to one scenario run."""
    truth = [TruthResult(t.label, t.kind, t.start_s, t.end_s, t.snr_db) for t in rendered.truth]
    shown = sorted((r for r in rows if r["t_display"] is not None), key=lambda r: r["t_detect"])
    alerts = [
        AlertRecord(
            r["id"], r["alert_class"], r["stage"], bool(r["confirmed"]), r["cancel_reason"],
            r["t_detect"],
        )
        for r in shown
    ]  # fmt: skip
    by_id = {r["id"]: r for r in shown}
    for t in truth:
        if not t.label:
            continue
        lo, hi = t.start_s, t.end_s + MATCH_TAIL_S
        t.detected = any(
            (not d.provisional) and d.alert_class.value == t.label and lo <= d.t_detect <= hi
            for d in detections
        )
        first = next(
            (a for a in alerts if a.alert_class == t.label and lo <= a.t_detect <= hi), None
        )
        if first is not None:
            row = by_id[first.event_id]
            t.stage = first.stage
            t.processing_ms = row["processing_ms"]
            t.a2d_est_ms = row["alarm_to_display_ms"]
            t.a2d_truth_ms = (row["t_display"] - t.start_s) * 1000.0
    for a in alerts:
        a.matched = any(
            t.label == a.alert_class and t.start_s <= a.t_detect <= t.end_s + MATCH_TAIL_S
            for t in truth
        )
    return truth, alerts


def run_scenario(
    rendered: RenderedScenario,
    cfg: SentinelConfig,
    classifier: Classifier | None = None,
) -> ScenarioResult:
    """Run one rendered scenario through the pipeline on a virtual clock and score it."""
    detections: list[Detection] = []
    log = EventLog(":memory:")
    stage = ClassifierStage(classifier, cfg.classifier) if classifier else None
    source = AudioFileSource(rendered.audio, cfg.audio.frame_ms, t0=0.0)
    pipeline = Pipeline(
        cfg,
        source,
        NullDisplay(),
        eventlog=log,
        classifier=stage,
        source_label="synthetic" if rendered.origin == "synthetic" else "replay",
        on_detection=detections.append,
    )
    cpu0 = time.process_time()
    pipeline.run_sync()
    cpu = time.process_time() - cpu0
    truth, alerts = score_scenario(rendered, detections, log.list(limit=1_000_000))
    return ScenarioResult(
        rendered.name,
        rendered.origin,
        rendered.split,
        rendered.duration_s,
        rendered.noise_type,
        rendered.noise_dbfs,
        truth,
        alerts,
        cpu,
        rendered.level,
    )


# ---- aggregation ----
def _pct(n: int, d: int) -> str:
    return "n/a" if d == 0 else f"{100.0 * n / d:.0f}% ({n}/{d})"


def _ms(v: float | None) -> str:
    return "n/a" if v is None else (f"{v:.1f} ms" if v < 10 else f"{v:.0f} ms")


@dataclass
class Aggregate:
    results: list[ScenarioResult]
    recall_by_snr: dict[tuple[str, float], tuple[int, int]] = field(default_factory=dict)
    recall_by_noise: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)
    recall_by_class: dict[str, tuple[int, int]] = field(default_factory=dict)
    neg_hours: float = 0.0
    neg_confirmed_false: int = 0
    neg_cancelled_false: int = 0
    pos_hours: float = 0.0
    pos_confirmed_false: int = 0
    pos_cancelled_false: int = 0
    audio_hours: float = 0.0
    cpu_fraction: float | None = None
    latency: dict[str, dict[str, list[float]]] = field(default_factory=dict)


def aggregate(results: list[ScenarioResult]) -> Aggregate:
    agg = Aggregate(results)
    latency: dict[str, dict[str, list[float]]] = {
        "tonal": {"proc": [], "truth": [], "est": []},
        "classifier": {"proc": [], "truth": [], "est": []},
    }
    for r in results:
        hours = r.duration_s / 3600.0
        agg.audio_hours += hours
        noise_key = f"{r.noise_type} {r.noise_dbfs:g} dBFS"
        if r.level:
            noise_key = f"{r.level}, {noise_key}"
        for t in r.truth:
            if not t.label:
                continue
            for table, key in (
                (agg.recall_by_snr, (t.label, t.snr_db)),
                (agg.recall_by_noise, (t.label, noise_key)),
                (agg.recall_by_class, t.label),
            ):
                n, d = table.get(key, (0, 0))  # type: ignore[arg-type]
                table[key] = (n + int(t.detected), d + 1)  # type: ignore[index]
            if t.stage is not None and t.detected:
                path = "classifier" if t.stage == "classifier" else "tonal"
                if t.processing_ms is not None:
                    latency[path]["proc"].append(t.processing_ms)
                if t.a2d_truth_ms is not None:
                    latency[path]["truth"].append(t.a2d_truth_ms)
                if t.a2d_est_ms is not None:
                    latency[path]["est"].append(t.a2d_est_ms)
        unmatched = [a for a in r.alerts if not a.matched]
        conf = sum(1 for a in unmatched if a.confirmed)
        canc = len(unmatched) - conf
        if r.negative_only:
            agg.neg_hours += hours
            agg.neg_confirmed_false += conf
            agg.neg_cancelled_false += canc
        else:
            agg.pos_hours += hours
            agg.pos_confirmed_false += conf
            agg.pos_cancelled_false += canc
    total_audio_s = agg.audio_hours * 3600.0
    agg.cpu_fraction = sum(r.cpu_s for r in results) / total_audio_s if total_audio_s else None
    agg.latency = latency
    return agg


# ---- threshold sweep ----
@dataclass
class SweepRow:
    alert_class: str
    threshold: float
    precision: float | None
    recall: float | None
    tp_events: int
    n_events: int
    fp_episodes: int
    episodes: int
    fp_per_hour: float | None


def window_scores(
    audio: np.ndarray, classifier: Classifier, hop_s: float
) -> tuple[np.ndarray, dict[AlertClass, np.ndarray]]:
    """Class scores at each classifier hop. Returns window end times (s) and per class scores."""
    hop = max(1, int(hop_s * SAMPLE_RATE))
    n = classifier.window_len
    ends = list(range(n, len(audio) + 1, hop))
    per: dict[AlertClass, list[float]] = {c: [] for c in classifier.class_indices}
    for end in ends:
        scores = class_scores(classifier.infer(audio[end - n : end]), classifier.class_indices)
        for c, v in scores.items():
            per[c].append(v)
    return (
        np.array([e / SAMPLE_RATE for e in ends]),
        {c: np.array(v, dtype=np.float64) for c, v in per.items()},
    )


def smoothed_flags(scores: np.ndarray, threshold: float, hits: int, windows: int) -> np.ndarray:
    """True where at least `hits` of the last `windows` scores reach the threshold."""
    above = (scores >= threshold).astype(int)
    out = np.zeros(len(scores), dtype=bool)
    for i in range(len(scores)):
        out[i] = above[max(0, i - windows + 1) : i + 1].sum() >= hits
    return out


@dataclass
class SweepItem:
    """What the sweep needs from one scenario: truth, length and the per-hop class scores."""

    truth: list[TruthResult]
    duration_s: float
    times: np.ndarray
    scores: dict[AlertClass, np.ndarray]


def sweep_thresholds(
    items: list[SweepItem],
    hits: int,
    windows: int,
    thresholds: list[float],
) -> list[SweepRow]:
    """Precision and recall per class and threshold, applying the same smoothing as the stage."""
    rows: list[SweepRow] = []
    classes = sorted({c for it in items for c in it.scores}, key=lambda c: c.value)
    hours = sum(it.duration_s for it in items) / 3600.0
    for cls in classes:
        for thr in thresholds:
            tp = n_events = episodes = fp = 0
            for it in items:
                times = it.times
                flags = smoothed_flags(it.scores[cls], thr, hits, windows)
                windows_truth = [
                    (t.start_s, t.end_s + MATCH_TAIL_S) for t in it.truth if t.label == cls.value
                ]
                n_events += len(windows_truth)
                tp += sum(
                    1 for lo, hi in windows_truth if np.any(flags & (times >= lo) & (times <= hi))
                )
                idx = np.flatnonzero(flags)
                if len(idx):
                    breaks = np.flatnonzero(np.diff(idx) > 1)
                    starts = np.r_[idx[0], idx[breaks + 1]]
                    stops = np.r_[idx[breaks], idx[-1]]
                    for a, b in zip(starts, stops, strict=True):
                        episodes += 1
                        t0, t1 = times[a], times[b]
                        if not any(t0 <= hi and t1 >= lo for lo, hi in windows_truth):
                            fp += 1
            rows.append(
                SweepRow(
                    cls.value,
                    thr,
                    None if episodes == 0 else (episodes - fp) / episodes,
                    None if n_events == 0 else tp / n_events,
                    tp,
                    n_events,
                    fp,
                    episodes,
                    fp / hours if hours else None,
                )
            )
    return rows


# ---- synthetic suite ----
def synthetic_suite(
    seeds: int = 3, neg_minutes: float = 10.0, quick: bool = False
) -> list[Scenario]:
    """A broad SYNTHETIC scenario set for development. Never a source of reported performance."""
    snrs = [0, 10] if quick else [-5, 0, 5, 10, 20]
    noises = ["pink"] if quick else ["white", "pink", "babble"]
    n_seeds = 1 if quick else seeds
    out: list[Scenario] = []
    for noise in noises:
        for snr in snrs:
            for seed in range(n_seeds):
                events = [
                    {"kind": "tonal_alarm", "start_s": 10.0 + 20 * i, "duration_s": 6.0,
                     "snr_db": snr, "label": "reversing_alarm"}
                    for i in range(3)
                ]  # fmt: skip
                out.append(
                    Scenario.model_validate(
                        {
                            "name": f"syn_tonal_{noise}_snr{snr}_s{seed}",
                            "duration_s": 70,
                            "seed": 100 + seed,
                            "noise": {"type": noise, "level_dbfs": -30},
                            "events": events,
                        }
                    )
                )
    if not quick:
        out.append(
            Scenario.model_validate(
                {
                    "name": "syn_classifier_standins_pink_snr10",
                    "duration_s": 60,
                    "seed": 5,
                    "noise": {"type": "pink", "level_dbfs": -30},
                    "events": [
                        {
                            "kind": "horn",
                            "start_s": 8,
                            "duration_s": 3,
                            "snr_db": 10,
                            "label": "vehicle_horn",
                        },
                        {
                            "kind": "fire_alarm_t3",
                            "start_s": 30,
                            "duration_s": 8,
                            "snr_db": 10,
                            "label": "fire_alarm",
                        },
                    ],
                }
            )
        )
    neg_s = max(30.0, neg_minutes * 60.0)
    for noise in ["pink"] if quick else ["white", "pink", "brown", "babble"]:
        for level in [-30] if quick else [-30, -20]:
            out.append(
                Scenario.model_validate(
                    {
                        "name": f"syn_negative_{noise}_{abs(level)}dBFS",
                        "duration_s": neg_s,
                        "seed": 900 + abs(level),
                        "noise": {"type": noise, "level_dbfs": level},
                    }
                )
            )
    out.append(
        Scenario.model_validate(
            {
                "name": "syn_negative_decoys",
                "duration_s": 120,
                "seed": 21,
                "noise": {"type": "pink", "level_dbfs": -30},
                "events": [
                    {"kind": "tone", "start_s": 0, "duration_s": 20, "snr_db": 10},
                    {"kind": "beep", "start_s": 40, "duration_s": 0.1, "snr_db": 10},
                    {"kind": "click", "start_s": 60, "snr_db": 10},
                ],
            }
        )
    )
    return out


# ---- report ----
def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No data._\n"
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def origin_banner(origin: str, splits: set[str | None]) -> str:
    if origin == "synthetic":
        return (
            "> **SYNTHETIC AUDIO.** Generated by `sentinel.sim.synth`. These numbers are for "
            "development and tests only. Do not quote them as performance."
        )
    split_txt = ", ".join(sorted(s or "unspecified" for s in splits))
    return (
        f"> **REAL RECORDINGS** (split: {split_txt}). Tune thresholds on the `dev` split. Only "
        "numbers from a `heldout` split, recorded on a different day and in a different room, may "
        "be reported."
    )


def _recall_tables(agg: Aggregate) -> tuple[str, str]:
    by_snr = [
        [cls, f"{snr:g} dB", str(n), str(d), _pct(n, d)]
        for (cls, snr), (n, d) in sorted(agg.recall_by_snr.items())
    ]
    by_noise = [
        [cls, key, str(n), str(d), _pct(n, d)]
        for (cls, key), (n, d) in sorted(agg.recall_by_noise.items())
    ]
    overall = [
        [cls, str(n), str(d), _pct(n, d)] for cls, (n, d) in sorted(agg.recall_by_class.items())
    ]
    a = _table(["Class", "SNR", "Detected", "Events", "Recall"], by_snr)
    b = _table(["Class", "Noise", "Detected", "Events", "Recall"], by_noise)
    c = _table(["Class", "Detected", "Events", "Recall"], overall)
    return (
        f"**By class and SNR**\n\n{a}\n**By class and noise level**\n\n{b}\n**By class**\n\n{c}",
        "",
    )


def _false_alert_table(agg: Aggregate) -> str:
    def rate(n: int, hours: float) -> str:
        return "n/a" if hours == 0 else f"{n / hours:.2f}"

    rows = [
        [
            "Negative-only scenarios", f"{agg.neg_hours:.2f}", str(agg.neg_confirmed_false),
            str(agg.neg_cancelled_false),
            rate(agg.neg_confirmed_false + agg.neg_cancelled_false, agg.neg_hours),
        ],
        [
            "Outside events in positive scenarios", f"{agg.pos_hours:.2f}",
            str(agg.pos_confirmed_false), str(agg.pos_cancelled_false),
            rate(agg.pos_confirmed_false + agg.pos_cancelled_false, agg.pos_hours),
        ],
    ]  # fmt: skip
    return _table(
        [
            "Audio",
            "Hours",
            "Confirmed false alerts",
            "Cancelled provisional alerts",
            "False alerts per hour (both)",
        ],
        rows,
    )


def _latency_table(agg: Aggregate) -> str:
    rows = []
    names = {"tonal": "Tonal path (gate first)", "classifier": "Classifier only"}
    for path in ("tonal", "classifier"):
        d = agg.latency.get(path, {"proc": [], "truth": [], "est": []})
        rows.append([
            names[path], str(len(d["truth"])),
            _ms(percentile(d["proc"], 50)), _ms(percentile(d["proc"], 95)),
            _ms(percentile(d["truth"], 50)), _ms(percentile(d["truth"], 95)),
            _ms(percentile(d["est"], 50)),
        ])  # fmt: skip
    return _table(
        [
            "Path",
            "Events",
            "Processing p50",
            "Processing p95",
            "Alarm to display p50",
            "Alarm to display p95",
            "p50 from estimated onset",
        ],
        rows,
    )


def _targets_table(agg: Aggregate, origin: str) -> str:
    real = origin == "real"

    def verdict(ok: bool | None) -> str:
        if not real:
            return "n/a (synthetic, not a claim)"
        return "not measured" if ok is None else ("meets goal" if ok else "below goal")

    rows: list[list[str]] = []
    for cls, (n, d) in sorted(agg.recall_by_class.items()):
        ok = None if d == 0 else n / d >= TARGET_RECALL
        rows.append(["R1", f"Recall {cls} at least 90%", _pct(n, d), verdict(ok)])
    for path, target in (
        ("tonal", TARGET_TONAL_MEDIAN_MS),
        ("classifier", TARGET_CLASSIFIER_MEDIAN_MS),
    ):
        med = percentile(agg.latency.get(path, {}).get("truth", []), 50)
        label = "Median alarm-to-display under " + (
            "500 ms (tonal)" if path == "tonal" else "1.5 s (classifier only)"
        )
        rows.append(["R2", label, _ms(med), verdict(None if med is None else med < target)])
    n_hours = agg.neg_hours
    n_false = agg.neg_confirmed_false + agg.neg_cancelled_false
    rate = None if n_hours == 0 else n_false / n_hours
    rows.append([
        "R3", "False alerts under 1 per 10 minutes (noisy room)",
        "n/a" if rate is None else f"{rate:.2f} per hour",
        verdict(None if rate is None else rate < TARGET_FALSE_ALERTS_PER_HOUR),
    ])  # fmt: skip
    return _table(["ID", "Goal", "Measured", "Status"], rows)


def _sweep_table(rows: list[SweepRow]) -> str:
    def f(v: float | None, spec: str) -> str:
        return "n/a" if v is None else format(v, spec)

    return _table(
        [
            "Class",
            "Threshold",
            "Precision",
            "Recall",
            "Events found",
            "False episodes",
            "False per hour",
        ],
        [
            [
                r.alert_class,
                f"{r.threshold:.2f}",
                f(r.precision, ".2f"),
                f(r.recall, ".2f"),
                f"{r.tp_events}/{r.n_events}",
                str(r.fp_episodes),
                f(r.fp_per_hour, ".2f"),
            ]
            for r in rows
        ],
    )


def scenario_table(results: list[ScenarioResult], limit: int = 60) -> str:
    rows = [
        [r.name, f"{r.duration_s:.0f} s", f"{r.noise_type} {r.noise_dbfs:g} dBFS",
         str(sum(1 for t in r.truth if t.label)), str(len(r.alerts)), r.split or "-"]
        for r in results[:limit]
    ]  # fmt: skip
    extra = (
        f"\n_{len(results) - limit} more scenarios not listed._\n" if len(results) > limit else ""
    )
    return (
        _table(["Scenario", "Length", "Noise", "Labelled events", "Alerts shown", "Split"], rows)
        + extra
    )


def render_report(
    template_text: str,
    origin: str,
    results: list[ScenarioResult],
    cfg: SentinelConfig,
    sweep: list[SweepRow] | None = None,
    when: str = "",
    commit: str = "unknown",
    classifier_used: bool = True,
) -> str:
    agg = aggregate(results)
    recall_md, _ = _recall_tables(agg)
    cpu = "n/a" if agg.cpu_fraction is None else f"{100 * agg.cpu_fraction:.1f}% of one core"
    notes = [
        "Latency here is software only (virtual stream clock plus compute). True end to end "
        "latency to photons on the glasses needs a hardware measurement.",
        "Alarm to display p50 and p95 use the scenario's ground-truth onset. The last column uses "
        "the detector's own estimated onset, which is what the event log stores.",
        "CPU is process CPU time divided by audio duration, measured with the pipeline running "
        "as fast as possible on one thread. It is an estimate of load at real time, not a "
        "measurement on the target device.",
    ]
    if not classifier_used:
        notes.append("The classifier was disabled for this run. Only the tonal gate was evaluated.")
    if origin == "synthetic":
        notes.append(
            "Synthetic stand-ins for horns and fire alarms are not expected to be recognised by a "
            "model trained on real audio. Low recall on those rows is not a model defect."
        )
    sweep_md = (
        "_Not requested. Run with `--sweep-thresholds`._\n"
        if sweep is None
        else _sweep_table(sweep)
    )
    splits = {r.split for r in results}
    tmpl = string.Template(template_text)
    return tmpl.safe_substitute(
        title=f"Sentinel evaluation report ({origin})",
        banner=origin_banner(origin, splits),
        when=when,
        commit=commit,
        config_hash=cfg.config_hash(),
        n_scenarios=str(len(results)),
        audio_hours=f"{agg.audio_hours:.2f}",
        scenarios=scenario_table(results),
        recall=recall_md,
        false_alerts=_false_alert_table(agg),
        latency=_latency_table(agg),
        cpu=cpu,
        targets=_targets_table(agg, origin),
        sweep=sweep_md,
        notes="\n".join(f"- {n}" for n in notes),
    )


def default_template() -> str:
    path = PROJECT_ROOT / "reports" / "TEMPLATE.md"
    return path.read_text() if path.exists() else FALLBACK_TEMPLATE


FALLBACK_TEMPLATE = """# $title

$banner

- Date: $when
- Code version: $commit
- Config hash: $config_hash
- Scenarios: $n_scenarios ($audio_hours hours of audio)

## Recall

$recall

## False alerts

$false_alerts

## Latency

$latency

CPU: $cpu

## Goals from the spec

$targets

## Threshold sweep

$sweep

## Notes

$notes

## Scenarios

$scenarios
"""


def render_scenarios(scenarios: list[Scenario]) -> list[RenderedScenario]:
    return [render(s) for s in scenarios]


def write_report(text: str, out_dir: Path, stem: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.md"
    path.write_text(text, encoding="utf-8")
    return path

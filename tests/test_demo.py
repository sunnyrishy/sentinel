"""M8: one-command demo mode, labeled replay fallback, offline guarantee."""

from __future__ import annotations

import copy
import importlib.util
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import yaml

from sentinel.adapters.audio_file import AudioFileSource
from sentinel.config import DEFAULT_CONFIG_PATH, load_config
from sentinel.eventlog import EventLog
from sentinel.sim.scenario import Scenario, render
from sentinel.types import SAMPLE_RATE

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def env(tmp_path: Path):
    port = free_port()
    raw = copy.deepcopy(yaml.safe_load(DEFAULT_CONFIG_PATH.read_text()))
    raw["dashboard"]["port"] = port
    raw["recording"]["dir"] = str(tmp_path / "rec")
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(yaml.safe_dump(raw))
    scenario = tmp_path / "s.yaml"
    scenario.write_text(
        yaml.safe_dump(
            {
                "name": "short_demo",
                "duration_s": 8,
                "seed": 3,
                "noise": {"type": "pink", "level_dbfs": -30},
                "events": [
                    {
                        "kind": "tonal_alarm",
                        "start_s": 0.8,
                        "duration_s": 3.0,
                        "snr_db": 10,
                        "label": "reversing_alarm",
                    }
                ],
            }
        )
    )
    return {
        "port": port,
        "cfg": str(cfg),
        "scenario": str(scenario),
        "db": str(tmp_path / "demo.sqlite"),
        "tmp": tmp_path,
    }


def run_in_thread(demo, argv):
    rc: list[int] = []
    t = threading.Thread(target=lambda: rc.append(demo.main(argv)))
    t.start()
    return t, rc


def wait_json(url: str, cond=lambda j: True, timeout: float = 20.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = httpx.get(url, timeout=2).json()
            if cond(last):
                return last
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.1)
    raise AssertionError(f"condition not met for {url}, last={last}")


def base_args(env, *extra: str) -> list[str]:
    return ["--config", env["cfg"], "--db", env["db"], "--no-browser", "--no-classifier", *extra]


def test_replay_mode_is_labeled_and_works_offline(env, capsys, monkeypatch) -> None:
    """M8 acceptance: one command starts everything; replay mode shows the RECORDING badge."""
    demo = load_tool("demo")
    real_connect = socket.socket.connect

    def loopback_only(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"network access attempted: {address}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    t, rc = run_in_thread(
        demo, base_args(env, "--mode", "replay", "--input", env["scenario"], "--duration", "9")
    )
    base = f"http://127.0.0.1:{env['port']}"
    status = wait_json(f"{base}/api/status")
    assert status["recording_badge"] is True and status["source"] == "synthetic"
    assert status["mode"] == "demo" and "short_demo" in status["input"]
    events = wait_json(f"{base}/api/events", lambda j: len(j) >= 1)
    assert events[0]["source"] == "synthetic" and events[0]["alert_class"] == "reversing_alarm"
    page = httpx.get(f"{base}/?kiosk=1").text
    assert "RECORDING" in page and "kiosk" in page
    t.join(timeout=30)
    assert rc == [0]
    out = capsys.readouterr().out
    assert "RECORDING: replaying audio. This is not the live microphone." in out
    assert f"http://127.0.0.1:{env['port']}/?kiosk=1" in out
    assert "no network calls" in out and "not a certified safety device" in out


def no_mic(*_args, **_kwargs):
    raise RuntimeError("Could not open the microphone: no input device")


def test_live_mode_without_microphone_falls_back_to_labeled_replay(
    env, capsys, monkeypatch
) -> None:
    demo = load_tool("demo")
    monkeypatch.setattr(demo, "make_audio_source", no_mic)
    t, rc = run_in_thread(
        demo, base_args(env, "--mode", "live", "--input", env["scenario"], "--duration", "5")
    )
    base = f"http://127.0.0.1:{env['port']}"
    status = wait_json(f"{base}/api/status")
    assert status["recording_badge"] is True and status["source"] != "live"
    t.join(timeout=30)
    assert rc == [0]
    out = capsys.readouterr()
    assert "NOT LIVE: the microphone could not be opened, so this is a replay." in out.out
    assert "Microphone unavailable" in out.err


def test_no_fallback_fails_instead_of_faking_live(env, capsys, monkeypatch) -> None:
    demo = load_tool("demo")
    monkeypatch.setattr(demo, "make_audio_source", no_mic)
    assert demo.main(base_args(env, "--mode", "live", "--no-fallback", "--duration", "2")) == 2
    assert "Could not open the microphone" in capsys.readouterr().err


def test_live_mode_with_a_microphone_is_not_badged_as_recording(env, monkeypatch) -> None:
    """With a working live source the page must not show RECORDING and events say 'live'."""
    demo = load_tool("demo")
    rendered = render(
        Scenario.model_validate(
            {
                "name": "fake_mic",
                "duration_s": 7,
                "seed": 3,
                "noise": {"type": "pink", "level_dbfs": -30},
                "events": [
                    {
                        "kind": "tonal_alarm",
                        "start_s": 0.8,
                        "duration_s": 3.0,
                        "snr_db": 10,
                        "label": "reversing_alarm",
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(
        demo, "make_audio_source", lambda *a, **k: AudioFileSource(rendered.audio, realtime=True)
    )
    t, rc = run_in_thread(demo, base_args(env, "--mode", "live", "--duration", "8"))
    base = f"http://127.0.0.1:{env['port']}"
    status = wait_json(f"{base}/api/status")
    assert status["source"] == "live" and status["recording_badge"] is False
    assert status["input"] == "live microphone"
    events = wait_json(f"{base}/api/events", lambda j: len(j) >= 1)
    assert events[0]["source"] == "live"
    t.join(timeout=30)
    assert rc == [0]


def test_demo_log_is_fresh_unless_kept(env) -> None:
    demo = load_tool("demo")
    log = EventLog(env["db"])
    log.insert(
        id="e_000099",
        alert_class="fire_alarm",
        stage="classifier",
        score=0.5,
        confirmed=True,
        source="synthetic",
        config_hash="old",
    )
    log.close()
    t, rc = run_in_thread(
        demo, base_args(env, "--mode", "replay", "--input", env["scenario"], "--duration", "3")
    )
    t.join(timeout=30)
    assert rc == [0]
    ids = [r["id"] for r in EventLog(env["db"]).list()]
    assert "e_000099" not in ids


def test_keep_log_keeps_old_events(env) -> None:
    demo = load_tool("demo")
    log = EventLog(env["db"])
    log.insert(
        id="e_000099",
        alert_class="fire_alarm",
        stage="classifier",
        score=0.5,
        confirmed=True,
        source="synthetic",
        config_hash="old",
    )
    log.close()
    t, rc = run_in_thread(
        demo,
        base_args(
            env, "--mode", "replay", "--input", env["scenario"], "--duration", "3", "--keep-log"
        ),
    )
    t.join(timeout=30)
    assert rc == [0] and "e_000099" in [r["id"] for r in EventLog(env["db"]).list()]


def test_strict_toggle_changes_smoothing_and_config_hash() -> None:
    demo = load_tool("demo")
    cfg = load_config()
    before = cfg.config_hash()
    demo.apply_strict(cfg)
    assert (cfg.classifier.smoothing.hits, cfg.classifier.smoothing.windows) == (3, 3)
    assert cfg.config_hash() != before
    assert load_config().classifier.smoothing.hits == 2  # the file is untouched


def test_looping_source_repeats_and_stops_on_close() -> None:
    demo = load_tool("demo")
    audio = render(
        Scenario.model_validate(
            {"name": "l", "duration_s": 0.2, "noise": {"type": "white", "level_dbfs": -40}}
        )
    ).audio
    src = demo.LoopingSource(audio, frame_ms=32, gap_s=0.0)
    n = 0
    for _ in src.frames():
        n += 1
        if n > 20:  # more frames than one pass of 0.2 s (7 frames) holds
            src.close()
    assert n >= 21 and len(audio) == int(0.2 * SAMPLE_RATE)


def test_demo_scenario_file_is_synthetic_and_valid() -> None:
    from sentinel.sim.scenario import load_scenario

    sc = load_scenario(ROOT / "scenarios" / "demo.yaml")
    assert sc.audio_origin == "synthetic" and sc.duration_s >= 30
    assert [e.label for e in sc.events if e.label] == ["reversing_alarm", "reversing_alarm"]
    assert (ROOT / "scenarios" / "demo.yaml").read_text().splitlines()[0].startswith("# SYNTHETIC")


def test_missing_input_file_is_a_clean_error(env, capsys) -> None:
    demo = load_tool("demo")
    rc = demo.main(base_args(env, "--mode", "replay", "--input", str(env["tmp"] / "nope.wav")))
    assert rc == 2
    assert capsys.readouterr().err.strip()

"""M7: stubs, bridge publisher, bridge echo subscriber, smoke test, latency probe."""

from __future__ import annotations

import copy
import importlib
import importlib.util
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from sentinel.adapters import make_audio_source, make_display
from sentinel.adapters.base import AsyncDisplay
from sentinel.adapters.display_bridge import BridgeDisplay
from sentinel.config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from sentinel.dashboard.server import DashboardServer, Hub, create_app
from sentinel.eventlog import EventLog
from sentinel.types import Alert, AlertClass, DisplayCaps

ROOT = Path(__file__).resolve().parents[1]
CFG = load_config()


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look their module up here
    spec.loader.exec_module(mod)
    return mod


def alert(blink=True) -> Alert:
    return Alert("e_000123", AlertClass.REVERSING_ALARM, 1, "BACKUP ALARM", "NEARBY", blink, 3.0)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---- stubs -------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "module", ["sentinel.adapters.audio_glasses", "sentinel.adapters.display_microlumin"]
)
def test_stubs_import_cleanly(module: str) -> None:
    assert importlib.import_module(module) is not None


def test_glasses_audio_stub_fails_clearly() -> None:
    from sentinel.adapters.audio_glasses import AudioGlassesSource

    with pytest.raises(NotImplementedError, match=r"SDK_NOTES\.md") as exc:
        AudioGlassesSource()
    assert "--input mic" in str(exc.value)
    for method in ("frames", "close"):
        assert callable(getattr(AudioGlassesSource, method))


def test_microlumin_display_stub_fails_clearly() -> None:
    from sentinel.adapters.display_microlumin import MicroLuminDisplay

    with pytest.raises(NotImplementedError, match=r"SDK_NOTES\.md") as exc:
        MicroLuminDisplay()
    assert "not implemented" in str(exc.value)
    for method in ("capabilities", "show", "clear", "close"):
        assert callable(getattr(MicroLuminDisplay, method))
        with pytest.raises(NotImplementedError):
            getattr(MicroLuminDisplay, method)(
                object.__new__(MicroLuminDisplay), *([alert()] if method == "show" else [])
            )


def test_factories_route_to_stubs_and_reject_unknown_names() -> None:
    with pytest.raises(NotImplementedError):
        make_display(CFG, "microlumin")
    with pytest.raises(NotImplementedError):
        make_audio_source(CFG, "glasses")
    with pytest.raises(ConfigError, match="unknown display adapter"):
        make_display(CFG, "hologram")
    with pytest.raises(ConfigError, match="unknown input"):
        make_audio_source(CFG, "telepathy")
    with pytest.raises(ConfigError, match="needs the dashboard"):
        make_display(CFG, "bridge")  # no hub to publish to


def test_stub_files_carry_todo_notes() -> None:
    for name in ("audio_glasses.py", "display_microlumin.py"):
        text = (ROOT / "src" / "sentinel" / "adapters" / name).read_text()
        assert "TODO" in text and "STUB" in text


def test_run_live_reports_stub_without_traceback(capsys) -> None:
    live = load_tool("run_live")
    assert live.main(["--input", "glasses", "--no-classifier", "--no-dashboard"]) == 2
    assert "not implemented" in capsys.readouterr().err
    assert live.main(["--display", "microlumin", "--no-classifier", "--no-dashboard"]) == 2
    assert "not implemented" in capsys.readouterr().err


def test_smoke_test_with_stub_exits_cleanly_and_names_the_adapter(capsys) -> None:
    smoke = load_tool("hw_smoke_test")
    assert smoke.main(["--display", "microlumin"]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED" in out and "'microlumin'" in out and "not implemented" in out


# ---- smoke test ---------------------------------------------------------------------------------
def test_smoke_test_prints_numbers_for_console(capsys) -> None:
    smoke = load_tool("hw_smoke_test")
    assert smoke.main(["--display", "console", "--hold-s", "0"]) == 0
    out = capsys.readouterr().out
    assert "1. show" in out and "2. clear" in out and "burst of 20" in out
    assert re.search(r"4\. numbers for 20 show and clear pairs", out)
    assert re.search(r"show\s+p50 [\d.]+ ms\s+p95 [\d.]+ ms\s+max [\d.]+ ms", out)


def test_smoke_test_counts_a_slow_adapter_and_errors() -> None:
    smoke = load_tool("hw_smoke_test")

    class Slow:
        def capabilities(self):
            return DisplayCaps()

        def show(self, a):
            time.sleep(0.01)
            if a.event_id.endswith("_3"):
                raise RuntimeError("link dropped")

        def clear(self, e=None): ...
        def close(self): ...

    r = smoke.run_smoke(Slow(), bursts=5, hold_s=0)
    assert len(r.show_ms) == 5 and min(r.show_ms) >= 10.0
    assert any("link dropped" in e for e in r.errors)


# ---- bridge publisher -------------------------------------------------------------------------
def test_bridge_display_payload_is_the_spec_message_and_truncates() -> None:
    sent: list[dict] = []
    d = BridgeDisplay(sent.append, DisplayCaps(max_chars_per_line=6, supports_blink=True))
    d.show(alert())
    d.clear("e_000123")
    assert sent[0] == {
        "type": "alert",
        "event_id": "e_000123",
        "line1": "BACKUP",
        "line2": "NEARBY",
        "tier": 1,
        "blink": True,
        "ttl_ms": 3000,
    }
    assert sent[1] == {"type": "clear", "event_id": "e_000123"}
    assert set(sent[0]) == {"type", "event_id", "line1", "line2", "tier", "blink", "ttl_ms"}


def test_bridge_payload_says_no_blink_when_display_cannot_blink() -> None:
    sent: list[dict] = []
    BridgeDisplay(sent.append, DisplayCaps(supports_blink=False)).show(alert(blink=True))
    assert sent[0]["blink"] is False  # the core emulates blinking with cycles instead


def test_bridge_blink_is_emulated_with_show_clear_cycles() -> None:
    sent: list[dict] = []
    inner = BridgeDisplay(sent.append, DisplayCaps(supports_blink=False))
    d = AsyncDisplay(inner, blink_period_s=0.05)
    d.show(alert(blink=True))
    time.sleep(0.4)
    d.clear("e_000123")
    time.sleep(0.1)
    kinds = [m["type"] for m in sent]
    assert kinds.count("alert") >= 3 and kinds.count("clear") >= 3
    d.close()


def test_make_display_bridge_uses_configured_caps() -> None:
    sent: list[dict] = []
    d = make_display(CFG, "bridge", sent.append)
    assert isinstance(d, BridgeDisplay) and d.capabilities().supports_blink is False


def test_console_display_never_emulates_blink() -> None:
    assert make_display(CFG, "console").capabilities().supports_blink is True


# ---- bridge echo: M7 acceptance -----------------------------------------------------------------
def run_echo(url: str, *extra: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(ROOT / "tools" / "bridge_echo.py"), "--url", url, *extra],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )  # fmt: skip


def wait_for(cond, timeout=8.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_bridge_echo_prints_alerts_published_by_the_core() -> None:
    hub = Hub()
    port = free_port()
    server = DashboardServer(create_app(EventLog(), hub, stats_interval_s=60), "127.0.0.1", port)
    server.start()
    echo = run_echo(f"ws://127.0.0.1:{port}/ws", "--count", "1", "--timeout", "20", "--all")
    try:
        assert wait_for(lambda: hub.subscriber_count >= 1)
        hub.publish({"type": "event", "event": {"id": "e_000123", "confirmed": False}})
        BridgeDisplay(hub.publish, DisplayCaps(supports_blink=True)).show(alert())
        out, _ = echo.communicate(timeout=15)
    finally:
        echo.kill()
        server.stop()
    assert "ALERT e_000123 tier 1 blink: BACKUP ALARM | NEARBY (ttl 3000 ms)" in out
    assert "event " in out and "connected" in out


def test_replay_with_bridge_display_reaches_echo_subscriber(tmp_path: Path) -> None:
    """Full chain: replay -> pipeline -> BridgeDisplay -> WebSocket -> bridge_echo."""
    port = free_port()
    raw = copy.deepcopy(yaml.safe_load(DEFAULT_CONFIG_PATH.read_text()))
    raw["dashboard"]["port"] = port
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(raw))
    scenario = tmp_path / "s.yaml"
    scenario.write_text(
        yaml.safe_dump(
            {
                "name": "bridge_e2e",
                "duration_s": 9,
                "seed": 3,
                "noise": {"type": "pink", "level_dbfs": -30},
                "events": [
                    {
                        "kind": "tonal_alarm",
                        "start_s": 1.0,
                        "duration_s": 3.0,
                        "snr_db": 10,
                        "label": "reversing_alarm",
                    }
                ],
            }
        )
    )
    echo = run_echo(
        f"ws://127.0.0.1:{port}/ws", "--count", "1", "--timeout", "25", "--retry-s", "0.2"
    )
    replay = load_tool("run_replay")
    rc: list[int] = []
    t = threading.Thread(
        target=lambda: rc.append(
            replay.main(
                [
                    str(scenario),
                    "--config",
                    str(cfg_path),
                    "--display",
                    "bridge",
                    "--realtime",
                    "--no-classifier",
                    "--db",
                    ":memory:",
                ]
            )
        )
    )
    t.start()
    try:
        out, _ = echo.communicate(timeout=30)
    finally:
        echo.kill()
        t.join(timeout=30)
    assert rc == [0]
    assert "ALERT e_000001 tier 1" in out and "BACKUP ALARM | NEARBY" in out


def test_bridge_echo_rejects_bad_address(capsys) -> None:
    echo = load_tool("bridge_echo")
    assert echo.main(["--url", "http://not-a-websocket", "--timeout", "2"]) == 2
    assert "Bad WebSocket address" in capsys.readouterr().err


def test_bridge_echo_describe_formats_all_message_types() -> None:
    echo = load_tool("bridge_echo")
    sample = {
        "type": "alert",
        "event_id": "e_1",
        "line1": "BACKUP ALARM",
        "line2": "NEARBY",
        "tier": 1,
        "blink": True,
        "ttl_ms": 3000,
    }
    assert "BACKUP ALARM | NEARBY" in echo.describe(sample)
    assert "clear all" in echo.describe({"type": "clear", "event_id": None})
    assert echo.describe({"type": "unknown"}) is None
    echo.forward_to_glasses({"type": "alert"})  # the stub must not raise


# ---- latency probe ------------------------------------------------------------------------------
def test_latency_probe_simulate_prints_timeline(capsys) -> None:
    probe = load_tool("latency_probe")
    rc = probe.main(["--probes", "1", "--interval", "8", "--display", "console", "--no-classifier"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "sound starts" in out and "show returned" in out
    m = re.search(r"Software alarm-to-display over 1 probes: median (\d+) ms", out)
    assert m and 150 <= int(m.group(1)) <= 900
    assert "slow-motion video" in out


def test_latency_probe_rejects_short_interval(capsys) -> None:
    probe = load_tool("latency_probe")
    assert probe.main(["--interval", "3"]) == 2
    assert "at least 8 s" in capsys.readouterr().err


def test_probe_audio_schedule() -> None:
    probe = load_tool("latency_probe")
    audio, onsets = probe.probe_audio(3, 10.0)
    assert onsets == [1.0, 11.0, 21.0] and len(audio) > 24 * 16000

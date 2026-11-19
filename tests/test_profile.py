"""Per-person profile, phone page, test alert and LAN addresses."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sentinel.adapters.audio_file import AudioFileSource
from sentinel.config import load_config
from sentinel.dashboard.server import Hub, create_app
from sentinel.eventlog import EventLog
from sentinel.pipeline import Pipeline
from sentinel.profile import ProfileError, ProfileStore
from sentinel.runtime import lan_addresses, start_runtime
from sentinel.sim import synth
from sentinel.types import SAMPLE_RATE, Alert, AlertClass, DisplayCaps

CFG = load_config()
R = AlertClass.REVERSING_ALARM


def free_port() -> int:
    import socket

    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


class Rec:
    def __init__(self) -> None:
        self.shown: list[Alert] = []

    def capabilities(self):
        return DisplayCaps(supports_blink=True)

    def show(self, a):
        self.shown.append(a)

    def clear(self, e=None): ...
    def close(self): ...


def run(audio, profile: ProfileStore | None) -> list[Alert]:
    d = Rec()
    p = Pipeline(CFG, AudioFileSource(audio, t0=0.0), d, eventlog=EventLog(":memory:"),
                 profile=profile, source_label="synthetic")  # fmt: skip
    p.run_sync()
    return d.shown


def tone_scene(snr_db: float, seed: int = 1) -> np.ndarray:
    sig = np.zeros(20 * SAMPLE_RATE, dtype=np.float32)
    a = synth.tonal_alarm(1900, 70, 6)
    sig[5 * SAMPLE_RATE : 5 * SAMPLE_RATE + len(a)] = a
    return synth.mix(sig, synth.noise("pink", 20.0, seed=seed), snr_db).audio


# ---- store -----------------------------------------------------------------------------------
def test_defaults_and_partial_update() -> None:
    s = ProfileStore()
    assert s.get() == {"enabled": {c.value: True for c in AlertClass}, "sensitivity": 1.0}
    out = s.update({"enabled": {"vehicle_horn": False}})
    assert out["enabled"]["vehicle_horn"] is False and out["enabled"]["fire_alarm"] is True
    assert not s.is_enabled(AlertClass.VEHICLE_HORN) and s.is_enabled(R)
    s.update({"sensitivity": 1.2})
    assert s.sensitivity == 1.2 and s.version == 2


BAD_UPDATES = [
    {"sensitivity": 5},
    {"sensitivity": "high"},
    {"sensitivity": True},
    {"enabled": {"siren": True}},
    {"enabled": {"fire_alarm": "yes"}},
    {"colour": 1},
    [],
]


@pytest.mark.parametrize("bad", BAD_UPDATES)
def test_invalid_updates_are_rejected_and_change_nothing(bad) -> None:
    s = ProfileStore()
    with pytest.raises(ProfileError):
        s.update(bad)
    assert s.get()["sensitivity"] == 1.0 and s.version == 0


def test_profile_persists_and_survives_a_damaged_file(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    ProfileStore(path).update({"sensitivity": 0.8, "enabled": {"fire_alarm": False}})
    again = ProfileStore(path)
    assert again.sensitivity == 0.8 and not again.is_enabled(AlertClass.FIRE_ALARM)
    path.write_text("{not json")
    assert ProfileStore(path).get()["sensitivity"] == 1.0
    path.write_text(json.dumps({"sensitivity": 99}))
    assert ProfileStore(path).get()["sensitivity"] == 1.0


# ---- effect on detection, live ----------------------------------------------------------------
def test_disabling_a_class_removes_its_alerts_and_events() -> None:
    audio = tone_scene(10)
    assert len(run(audio, ProfileStore())) == 1
    off = ProfileStore()
    off.update({"enabled": {"reversing_alarm": False}})
    d = Rec()
    log = EventLog(":memory:")
    Pipeline(CFG, AudioFileSource(audio, t0=0.0), d, eventlog=log, profile=off).run_sync()
    assert d.shown == [] and log.count() == 0


def test_sensitivity_changes_what_the_gate_catches() -> None:
    audio = tone_scene(-18)  # a very weak alarm buried in noise
    lenient = ProfileStore()
    lenient.update({"sensitivity": 1.5})
    stricter = ProfileStore()
    stricter.update({"sensitivity": 0.5})
    assert len(run(audio, stricter)) <= len(run(audio, lenient))
    assert len(run(tone_scene(10), stricter)) == 1  # a clear alarm still gets through


def test_sensitivity_is_read_live_by_the_classifier_stage() -> None:
    from sentinel.classify.yamnet import ClassifierStage

    class Fake:
        window_len = 8000
        class_indices = {R: [0]}

        def infer(self, w):
            return np.zeros(521, dtype=np.float32)

    stage = ClassifierStage(Fake(), CFG.classifier)
    store = ProfileStore()
    stage.profile = store
    score = {c: 0.0 for c in AlertClass}
    score[R] = 0.2  # below the 0.30 threshold at sensitivity 1.0
    assert stage.update(score, 1.0, 1.0) == [] and stage.update(score, 1.25, 1.25) == []
    store.update({"sensitivity": 1.5})  # effective threshold 0.20
    stage.update(score, 1.5, 1.5)
    assert [d.alert_class for d in stage.update(score, 1.75, 1.75)] == [R]


# ---- HTTP ------------------------------------------------------------------------------------
@pytest.fixture
def client():
    store, hub, shown = ProfileStore(), Hub(), []
    app = create_app(EventLog(), hub, profile=store, test_alert=lambda: shown.append("sent"),
                     stats_interval_s=60)  # fmt: skip
    with TestClient(app) as c:
        yield c, store, hub, shown


def test_profile_endpoints_and_broadcast(client) -> None:
    c, store, _, _ = client
    assert c.get("/api/profile").json()["sensitivity"] == 1.0
    with c.websocket_connect("/ws") as ws:
        ws.receive_json()
        r = c.post("/api/profile", json={"sensitivity": 1.3, "enabled": {"vehicle_horn": False}})
        assert r.status_code == 200 and r.json()["sensitivity"] == 1.3
        msg = ws.receive_json()
        assert msg["type"] == "profile" and msg["enabled"]["vehicle_horn"] is False
    assert store.sensitivity == 1.3
    bad = c.post("/api/profile", json={"sensitivity": 9})
    assert bad.status_code == 422 and "between" in bad.json()["detail"]
    assert store.sensitivity == 1.3


def test_test_alert_endpoint(client) -> None:
    c, _, _, shown = client
    assert c.post("/api/test-alert").json() == {"sent": True} and shown == ["sent"]
    no = TestClient(create_app(EventLog(), Hub(), stats_interval_s=60))
    assert no.post("/api/test-alert").status_code == 503
    assert no.post("/api/profile", json={}).status_code == 503


def test_phone_page_served_and_linked(client) -> None:
    c, *_ = client
    page = c.get("/phone")
    assert page.status_code == 200 and "Sentinel" in page.text and "/api/profile" in page.text
    assert "navigator.vibrate" in page.text and "not a certified safety device" in page.text
    assert 'href="/phone"' in c.get("/").text


# ---- runtime -----------------------------------------------------------------------------------
def test_runtime_test_alert_goes_through_the_display_and_clears(monkeypatch) -> None:
    import time

    rt = start_runtime(
        CFG.model_copy(deep=True), "live", "console", ":memory:", use_dashboard=False
    )
    seen: list[str] = []
    rt.display = type(
        "D",
        (),
        {
            "show": lambda s, a: seen.append("show " + a.line1),
            "clear": lambda s, e=None: seen.append("clear " + str(e)),
        },
    )()
    import sentinel.runtime as r

    monkeypatch.setattr(
        r.threading, "Timer", lambda delay, fn: type("T", (), {"start": lambda s: fn()})()
    )
    rt.send_test_alert()
    assert seen == ["show TEST ALERT", "clear e_test"]
    time.sleep(0)
    rt.close()


def test_lan_flag_binds_all_interfaces_and_lists_phone_urls() -> None:
    cfg = CFG.model_copy(deep=True)
    cfg.dashboard.port = free_port()
    rt = start_runtime(cfg, "live", "console", ":memory:", serve=True, lan=True)
    try:
        assert rt.server.host == "0.0.0.0"
        assert rt.phone_urls and all(
            u.endswith(f":{cfg.dashboard.port}/phone") for u in rt.phone_urls
        )
    finally:
        rt.close()
    local = start_runtime(CFG.model_copy(deep=True), "live", "console", ":memory:", serve=True)
    try:
        assert local.phone_urls == []  # loopback only unless --lan is given
    finally:
        local.close()


def test_lan_addresses_never_raises() -> None:
    assert lan_addresses()

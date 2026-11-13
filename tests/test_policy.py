"""Alert state machine tests. Time is injected, so every case is exact."""

from __future__ import annotations

import itertools

import pytest

from sentinel.config import PolicyConfig
from sentinel.policy import (
    ClassState,
    ClearAlert,
    CreateEvent,
    Policy,
    ShowAlert,
    UpdateEvent,
)
from sentinel.types import AlertClass, Detection

R, H, F = AlertClass.REVERSING_ALARM, AlertClass.VEHICLE_HORN, AlertClass.FIRE_ALARM


def make_policy(**kw) -> Policy:
    counter = itertools.count(1)
    cfg = PolicyConfig(
        cooldown_s=kw.get("cooldown_s", 5.0),
        ttl_s=kw.get("ttl_s", 3.0),
        blink=kw.get("blink", True),
        priority=["fire_alarm", "reversing_alarm", "vehicle_horn"],
    )
    return Policy(
        cfg,
        confirm_timeout_s=kw.get("confirm_timeout_s", 1.6),
        id_gen=lambda: f"e_{next(counter):06d}",
    )


def det(cls: AlertClass, stage: str, provisional: bool = False, t: float = 0.0, score=0.8):
    return Detection(cls, stage, score, provisional, t_onset=t - 0.3, t_detect=t)


def kinds(actions) -> list[str]:
    return [type(a).__name__ for a in actions]


def shows(actions):
    return [a for a in actions if isinstance(a, ShowAlert)]


def test_provisional_is_shown_immediately_with_unconfirmed_event() -> None:
    p = make_policy()
    acts = p.on_detection(det(R, "gate", provisional=True), now=10.0)
    assert kinds(acts) == ["CreateEvent", "ShowAlert"]
    create, show = acts
    assert create.confirmed is False and create.stage == "gate" and create.cancel_reason is None
    assert show.alert.line1 == "BACKUP ALARM" and show.alert.line2 == "NEARBY"
    assert show.alert.tier == 1 and show.alert.blink is True and show.alert.ttl_s == 3.0
    assert p.state(R) == ClassState.PROVISIONAL and p.displayed_class == R


def test_provisional_cancelled_on_timeout() -> None:
    p = make_policy()
    p.on_detection(det(R, "gate", provisional=True), now=10.0)
    assert p.tick(11.5) == []  # not yet: timer is 1.6 s
    acts = p.tick(11.7)
    assert kinds(acts) == ["ClearAlert", "UpdateEvent"]
    assert acts[0].reason == "timeout" and acts[1].changes == {"cancel_reason": "timeout"}
    assert p.state(R) == ClassState.COOLDOWN and p.displayed_class is None


def test_provisional_confirmed_by_second_gate_pulse() -> None:
    p = make_policy()
    p.on_detection(det(R, "gate", provisional=True), now=10.0)
    acts = p.on_detection(det(R, "gate", provisional=False), now=11.1)
    updates = [a for a in acts if isinstance(a, UpdateEvent)]
    assert updates[0].changes["confirmed"] is True and "stage" not in updates[0].changes
    assert shows(acts) == []  # no second alert
    assert p.state(R) == ClassState.CONFIRMED
    assert p.tick(11.8) == []  # the confirmation timer no longer cancels it


def test_provisional_confirmed_by_classifier_records_both() -> None:
    p = make_policy()
    p.on_detection(det(R, "gate", provisional=True), now=10.0)
    acts = p.on_detection(det(R, "classifier"), now=11.0)
    upd = next(a for a in acts if isinstance(a, UpdateEvent))
    assert upd.changes["confirmed"] is True and upd.changes["stage"] == "both"


def test_stage_upgrades_to_both_when_classifier_arrives_after_gate_confirmation() -> None:
    p = make_policy()
    p.on_detection(det(R, "gate", provisional=True), now=10.0)
    p.on_detection(det(R, "gate"), now=10.9)
    acts = p.on_detection(det(R, "classifier"), now=11.5)
    assert next(a for a in acts if isinstance(a, UpdateEvent)).changes["stage"] == "both"


def test_classifier_only_alert_creates_confirmed_event() -> None:
    p = make_policy()
    acts = p.on_detection(det(H, "classifier"), now=5.0)
    create = acts[0]
    assert isinstance(create, CreateEvent)
    assert create.confirmed is True and create.stage == "classifier"
    assert shows(acts)[0].alert.line1 == "VEHICLE HORN"
    assert p.state(H) == ClassState.CONFIRMED


def test_ttl_extension_and_clear() -> None:
    p = make_policy(ttl_s=3.0)
    p.on_detection(det(H, "classifier"), now=0.0)
    p.on_detection(det(H, "classifier"), now=2.0)  # extends to 5.0
    assert p.tick(4.9) == []
    acts = p.tick(5.1)
    assert kinds(acts) == ["ClearAlert"] and acts[0].reason == "ttl"
    assert p.state(H) == ClassState.COOLDOWN


def test_cooldown_blocks_new_events_then_releases() -> None:
    p = make_policy(ttl_s=3.0, cooldown_s=5.0)
    p.on_detection(det(H, "classifier"), now=0.0)
    p.tick(3.1)  # clears at 3.0, cooldown until 8.0
    assert p.on_detection(det(H, "classifier"), now=4.0) == []
    assert p.on_detection(det(H, "classifier"), now=7.9) == []
    assert p.state(H) == ClassState.COOLDOWN
    acts = p.on_detection(det(H, "classifier"), now=8.1)
    assert kinds(acts) == ["CreateEvent", "ShowAlert"]


def test_cooldown_also_follows_a_cancelled_provisional() -> None:
    p = make_policy()
    p.on_detection(det(R, "gate", provisional=True), now=0.0)
    p.tick(2.0)  # timeout at 1.6, cooldown until 6.6
    assert p.on_detection(det(R, "gate", provisional=True), now=3.0) == []
    assert shows(p.on_detection(det(R, "gate", provisional=True), now=7.0))


def test_higher_priority_replaces_lower_and_logs_superseded() -> None:
    p = make_policy()
    p.on_detection(det(H, "classifier"), now=0.0)
    first_id = "e_000001"
    acts = p.on_detection(det(F, "classifier"), now=1.0)
    assert kinds(acts) == ["UpdateEvent", "CreateEvent", "ShowAlert"]
    assert acts[0].event_id == first_id and acts[0].changes == {"cancel_reason": "superseded"}
    assert shows(acts)[0].alert.line1 == "FIRE ALARM"
    assert p.displayed_class == F and p.state(H) == ClassState.COOLDOWN


def test_reversing_outranks_horn() -> None:
    p = make_policy()
    p.on_detection(det(H, "classifier"), now=0.0)
    acts = p.on_detection(det(R, "gate", provisional=True), now=0.5)
    assert shows(acts)[0].alert.line1 == "BACKUP ALARM"


def test_lower_priority_while_higher_is_shown_is_logged_but_not_shown() -> None:
    p = make_policy()
    p.on_detection(det(F, "classifier"), now=0.0)
    acts = p.on_detection(det(H, "classifier"), now=0.5)
    assert kinds(acts) == ["CreateEvent"]
    assert acts[0].cancel_reason == "superseded" and acts[0].confirmed is True
    assert p.displayed_class == F


def test_only_one_alert_on_screen_at_a_time() -> None:
    p = make_policy()
    on_screen: list[str] = []
    stream = [
        (0.0, H, "classifier"),
        (0.5, R, "gate"),
        (1.0, F, "classifier"),
        (1.5, H, "classifier"),
        (2.0, R, "gate"),
        (2.5, F, "classifier"),
    ]
    for t, cls, stage in stream:
        for a in p.on_detection(det(cls, stage, provisional=(stage == "gate"), t=t), now=t):
            if isinstance(a, ShowAlert):
                on_screen = [a.alert.event_id]  # a show replaces whatever was there
            if isinstance(a, ClearAlert) and a.event_id in on_screen:
                on_screen = []
        assert len(on_screen) <= 1
        assert (p.displayed_class is None) == (not on_screen)


def test_every_event_is_logged() -> None:
    p = make_policy()
    creates = []
    for t, cls in [(0.0, H), (0.5, F), (0.6, R), (0.7, H)]:
        creates += [
            a
            for a in p.on_detection(det(cls, "classifier", t=t), now=t)
            if isinstance(a, CreateEvent)
        ]
    assert len(creates) == 3  # H, F, R; the second H is in cooldown, no new event


def test_blink_flag_follows_config() -> None:
    p = make_policy(blink=False)
    assert shows(p.on_detection(det(F, "classifier"), now=0.0))[0].alert.blink is False


def test_ttl_shorter_than_confirm_timer_still_ends_as_timeout() -> None:
    p = make_policy(ttl_s=1.0, confirm_timeout_s=1.6)
    p.on_detection(det(R, "gate", provisional=True), now=0.0)
    acts = p.tick(1.2)
    assert acts[1].changes == {"cancel_reason": "timeout"}


def test_tick_resolves_in_one_call_after_a_long_gap() -> None:
    p = make_policy()
    p.on_detection(det(R, "gate", provisional=True), now=0.0)
    p.tick(100.0)
    assert p.state(R) == ClassState.IDLE
    assert p.displayed_class is None


@pytest.mark.parametrize("cls", list(AlertClass))
def test_wording_matches_spec(cls: AlertClass) -> None:
    p = make_policy()
    alert = shows(p.on_detection(det(cls, "classifier"), now=0.0))[0].alert
    assert alert.line2 == "NEARBY" and alert.tier == 1
    assert alert.line1 == {R: "BACKUP ALARM", H: "VEHICLE HORN", F: "FIRE ALARM"}[cls]

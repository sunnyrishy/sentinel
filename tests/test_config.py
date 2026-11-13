from __future__ import annotations

import copy

import pytest
import yaml

from sentinel.__main__ import main
from sentinel.config import DEFAULT_CONFIG_PATH, ConfigError, config_from_dict, load_config
from sentinel.types import ALERT_WORDING, AlertClass


def raw_default() -> dict:
    return copy.deepcopy(yaml.safe_load(DEFAULT_CONFIG_PATH.read_text()))


def test_default_config_loads_and_hashes_stably() -> None:
    cfg = load_config()
    assert cfg.gate.band_hz == (800.0, 3000.0)
    assert cfg.classifier.classes["reversing_alarm"].names == ["Reversing beeps"]
    assert cfg.config_hash() == load_config().config_hash()


def test_hash_changes_with_config() -> None:
    raw = raw_default()
    raw["gate"]["peak_to_median_db"] = 20
    assert config_from_dict(raw).config_hash() != load_config().config_hash()


def test_unknown_top_level_key_fails_with_message() -> None:
    raw = raw_default()
    raw["gatee"] = {}
    with pytest.raises(ConfigError, match="gatee"):
        config_from_dict(raw)


def test_unknown_nested_key_fails_with_path() -> None:
    raw = raw_default()
    raw["gate"]["peak_to_median"] = 3
    with pytest.raises(ConfigError, match=r"gate\.peak_to_median"):
        config_from_dict(raw)


def test_unknown_class_name_fails_and_lists_valid_ones() -> None:
    raw = raw_default()
    raw["classifier"]["classes"]["siren"] = {"names": ["Siren"], "threshold": 0.3}
    with pytest.raises(ConfigError, match="unknown alert class 'siren'") as exc:
        config_from_dict(raw)
    assert "reversing_alarm" in str(exc.value)


def test_unknown_class_in_priority_fails() -> None:
    raw = raw_default()
    raw["policy"]["priority"] = ["fire_alarm", "klaxon"]
    with pytest.raises(ConfigError, match="klaxon"):
        config_from_dict(raw)


def test_bad_ranges_fail() -> None:
    raw = raw_default()
    raw["gate"]["burst_min_s"] = 2.0
    with pytest.raises(ConfigError, match="burst_min_s"):
        config_from_dict(raw)


def test_missing_file_is_clear(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_every_alert_class_has_wording() -> None:
    assert set(ALERT_WORDING) == set(AlertClass)
    for tier, line1, line2 in ALERT_WORDING.values():
        assert tier == 1 and line2 == "NEARBY" and len(line1) <= 12


def test_cli_help_prints_usage(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_cli_check_config(capsys) -> None:
    assert main(["check-config"]) == 0
    assert "config ok" in capsys.readouterr().out

import os

import quickres.config as config


def _use_tmp_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", os.path.join(str(tmp_path), "config.json"))


def test_load_config_logs_and_returns_empty_dict_on_corrupt_json(monkeypatch, tmp_path):
    # Mirrors load_pending()'s existing logging behavior for the identical
    # failure mode: a corrupted/unreadable config.json used to be swallowed
    # by a bare "except Exception: pass" with zero trace in quickres.log,
    # so get_initial_state() silently reset the user's theme/language/hotkey
    # to defaults on every boot with no way to diagnose why.
    _use_tmp_paths(monkeypatch, tmp_path)

    with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    log_calls = []
    monkeypatch.setattr(config, "log_msg", lambda msg: log_calls.append(msg))

    result = config.load_config()

    assert result == {}
    assert len(log_calls) == 1
    assert "config.json" in log_calls[0]


def test_load_config_returns_empty_dict_when_missing_and_does_not_log(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    log_calls = []
    monkeypatch.setattr(config, "log_msg", lambda msg: log_calls.append(msg))

    assert config.load_config() == {}
    assert log_calls == []


def test_load_config_round_trips_valid_config(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    cfg = {"theme": "dark", "language": "en"}
    assert config.save_config(cfg) is True

    assert config.load_config() == cfg

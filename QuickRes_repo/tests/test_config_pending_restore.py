import os

import quickres.config as config


def _use_tmp_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "PENDING_PATH", os.path.join(str(tmp_path), "pending_restore.json"))


def test_save_then_load_round_trips_exact_dict(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    record = {
        "action": "disable",
        "targets": [{"instance_id": "DISPLAY\\DEL4110\\5&2e2fefea&0&UID1078018", "friendly_name": "Dell"}],
        "owner_pid": 111,
        "started_at": 1000.0,
        "unlocked_at": None,
    }
    assert config.save_pending(record) is True

    loaded = config.load_pending()
    assert loaded == record


def test_save_returns_false_on_write_failure(monkeypatch, tmp_path):
    # Callers rely on this return value to refuse a disable action when the
    # crash-recovery record can't be persisted — the safety net is void
    # otherwise, so this must surface as False, not a swallowed error.
    _use_tmp_paths(monkeypatch, tmp_path)

    def _raise_replace(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", _raise_replace)

    assert config.save_pending({"action": "disable"}) is False


def test_load_returns_none_when_missing(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    assert config.load_pending() is None


def test_load_returns_none_on_corrupt_json(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    with open(config.PENDING_PATH, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    assert config.load_pending() is None


def test_clear_pending_removes_file_and_subsequent_load_is_none(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    config.save_pending({"action": "disable"})
    assert os.path.exists(config.PENDING_PATH)

    config.clear_pending()

    assert not os.path.exists(config.PENDING_PATH)
    assert config.load_pending() is None


def test_clear_pending_is_safe_when_already_absent(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    # Should not raise even though the file doesn't exist.
    config.clear_pending()


def test_pending_mtime_is_none_before_any_save(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    assert config.pending_mtime() is None


def test_pending_mtime_reflects_file_mtime_after_save(monkeypatch, tmp_path):
    _use_tmp_paths(monkeypatch, tmp_path)

    config.save_pending({"action": "disable"})

    mtime = config.pending_mtime()
    assert mtime is not None
    assert mtime == os.path.getmtime(config.PENDING_PATH)

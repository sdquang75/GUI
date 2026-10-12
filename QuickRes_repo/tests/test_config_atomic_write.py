import glob
import json
import os

import quickres.config as config


def test_successful_write_creates_target_with_correct_content_and_no_leftover_tmp(tmp_path):
    target = os.path.join(str(tmp_path), "out.json")
    data = {"action": "disable", "targets": [{"instance_id": "id-0"}]}

    result = config.write_json_atomic(target, data)

    assert result is True
    assert os.path.exists(target)
    with open(target, "r", encoding="utf-8") as f:
        assert json.load(f) == data
    leftover_tmp_files = glob.glob(os.path.join(str(tmp_path), "*.tmp*"))
    assert leftover_tmp_files == []


def test_write_failure_returns_false_and_does_not_corrupt_target(tmp_path, monkeypatch):
    target = os.path.join(str(tmp_path), "out.json")
    # Pre-existing good content that must survive a failed write attempt.
    with open(target, "w", encoding="utf-8") as f:
        json.dump({"pre-existing": True}, f)

    def _raise_replace(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(config.os, "replace", _raise_replace)

    result = config.write_json_atomic(target, {"new": "data"})

    assert result is False
    with open(target, "r", encoding="utf-8") as f:
        assert json.load(f) == {"pre-existing": True}


def test_write_failure_removes_temp_file(tmp_path, monkeypatch):
    target = os.path.join(str(tmp_path), "out.json")

    def _raise_replace(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(config.os, "replace", _raise_replace)

    config.write_json_atomic(target, {"new": "data"})

    leftover_tmp_files = glob.glob(os.path.join(str(tmp_path), "*.tmp*"))
    assert leftover_tmp_files == []

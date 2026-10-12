import os
import time

import pytest

from quickres.webview.bridge import Api
from quickres import config
from quickres import recovery


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


def _valid_record(**overrides):
    record = {
        "action": "disable",
        "targets": [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "Monitor A"}],
        "result_file": "monitor_op_result_111_222.json",
        "helper_pid": 999,
        "owner_pid": 111,
        "started_at": time.time(),
        "unlocked_at": None,
    }
    record.update(overrides)
    return record


def test_liveness_probe_precedes_result_file_read(monkeypatch):
    # REC-3 (load-bearing ordering): process_liveness MUST be called before
    # read_op_result, regardless of what either returns.
    call_order = []
    monkeypatch.setattr(config, "load_pending", lambda: _valid_record())
    monkeypatch.setattr(config, "pending_mtime", lambda: time.time())
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.process_liveness",
        lambda helper_pid, owner_pid, **kwargs: call_order.append("liveness") or recovery.Liveness.ALIVE,
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.read_op_result",
        lambda path, app_dir: call_order.append("read_result") or None,
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.sample_device_states",
        lambda ids: {iid: None for iid in ids},
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.recovery.is_safe_result_path", lambda path, app_dir: True
    )
    api = Api()

    api.recover_on_boot()

    assert call_order == ["liveness", "read_result"]


def test_unrecognized_record_is_notice_only_and_does_not_rearm_lock():
    # REC-8: a legacy/unrecognized shape must not re-arm the lock.
    config.save_pending({"schema": 1, "instance_id": "legacy-shape"})
    api = Api()

    result = api.recover_on_boot()

    assert result["outcomes"] == []
    assert api._op_lock.locked() is False
    assert config.load_pending() is None  # cleared


def test_in_flight_outcome_rearms_the_lock(monkeypatch):
    monkeypatch.setattr(config, "load_pending", lambda: _valid_record())
    monkeypatch.setattr(config, "pending_mtime", lambda: time.time())
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.process_liveness",
        lambda helper_pid, owner_pid, **kwargs: recovery.Liveness.ALIVE,
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.read_op_result", lambda path, app_dir: None
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.sample_device_states",
        lambda ids: {iid: None for iid in ids},
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.recovery.is_safe_result_path", lambda path, app_dir: True
    )
    api = Api()

    result = api.recover_on_boot()

    assert any(o["resolution"] == "in_flight" for o in result["outcomes"])
    assert api._op_lock.locked() is True
    # 1a: IN_FLIGHT re-arming must also flag boot_armed so bridge_op's
    # boot_armed_bypass escape hatches (recheck_pending/keep_disabled/
    # revert_now/force_unlock_pending) know to bypass the busy-check.
    assert api._boot_armed is True


def test_stale_dead_record_resolves_unconfirmable_and_does_not_rearm(monkeypatch):
    old_started_at = time.time() - 500  # well past the 120s expiry window
    monkeypatch.setattr(config, "load_pending", lambda: _valid_record(started_at=old_started_at))
    monkeypatch.setattr(config, "pending_mtime", lambda: old_started_at)
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.process_liveness",
        lambda helper_pid, owner_pid, **kwargs: recovery.Liveness.DEAD,
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.read_op_result", lambda path, app_dir: None
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.sample_device_states",
        lambda ids: {iid: None for iid in ids},
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.recovery.is_safe_result_path", lambda path, app_dir: True
    )
    api = Api()

    result = api.recover_on_boot()

    assert all(o["resolution"] == "unconfirmable" for o in result["outcomes"])
    assert result["force_unlockable"] is True
    assert api._op_lock.locked() is False


# ---------------------------------------------------------------------------
# A pending_restore.json record with a malformed field type (disk/AV
# corruption that still leaves valid JSON, a partial write from an older
# build, or manual editing) must not crash recover_on_boot() -- it runs
# inline inside get_initial_state, the sole boot RPC, and a raised exception
# here previously reached bridge_op's generic except-clause, failing the
# entire boot call with no way for the panel's Retry button to make
# progress (the on-disk record was never mutated before the crash, so a
# retry re-read the identical broken record and crashed identically again).
# These deliberately do NOT monkeypatch monitors.process_liveness/
# sample_device_states/read_op_result -- unlike the other tests in this
# file, they exercise the real production seam (including the real ctypes
# calls) end to end.
# ---------------------------------------------------------------------------


def test_malformed_started_at_does_not_crash_recover_on_boot():
    config.save_pending(_valid_record(started_at="not-a-timestamp"))
    api = Api()

    result = api.recover_on_boot()

    assert isinstance(result, dict)
    assert "outcomes" in result


def test_malformed_helper_pid_does_not_crash_recover_on_boot():
    # owner_pid must match this process's real pid so process_liveness
    # actually reaches its helper_pid type check instead of short-circuiting
    # to UNKNOWN on the owner_pid mismatch first.
    config.save_pending(_valid_record(owner_pid=os.getpid(), helper_pid="not-a-pid"))
    api = Api()

    result = api.recover_on_boot()

    assert isinstance(result, dict)
    assert "outcomes" in result

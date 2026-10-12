import ctypes
import uuid

import pytest

import json
import os

import quickres.monitors as monitors_mod
from quickres import recovery
from quickres.monitors import (
    GUID_DEVCLASS_MONITOR,
    _make_guid,
    _normalize_interface_id,
    enumerate_monitors,
    make_result_filename,
    read_op_result,
    set_monitors_enabled,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            r"\\?\DISPLAY#DEL4110#5&2e2fefea&0&UID1078018#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}",
            r"DISPLAY\DEL4110\5&2e2fefea&0&UID1078018",
        ),
        (
            r"\\?\DISPLAY#SAM0E5D#4&1a2b3c4d&0&UID4352#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}",
            r"DISPLAY\SAM0E5D\4&1a2b3c4d&0&UID4352",
        ),
        (
            r"\\?\DISPLAY#AUS2609#5&1f2e3d4c&0&UID37#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}",
            r"DISPLAY\AUS2609\5&1f2e3d4c&0&UID37",
        ),
    ],
)
def test_normalize_interface_id(raw, expected):
    assert _normalize_interface_id(raw) == expected


def test_make_guid_round_trip():
    g = _make_guid(GUID_DEVCLASS_MONITOR)
    raw_bytes = ctypes.string_at(ctypes.byref(g), ctypes.sizeof(g))
    round_tripped = uuid.UUID(bytes_le=raw_bytes)
    assert round_tripped == uuid.UUID(GUID_DEVCLASS_MONITOR)


# ---------------------------------------------------------------------------
# T2.2 enumerate_monitors() -- safe-omit default, injectable seam
# ---------------------------------------------------------------------------


def test_enumerate_monitors_all_queryable_returns_all(monkeypatch):
    raw = [
        ("DISPLAY\\A\\1", "Monitor A", 1001),
        ("DISPLAY\\B\\2", "Monitor B", 1002),
    ]
    statuses = {1001: True, 1002: False}

    monkeypatch.setattr(monitors_mod, "_list_raw_monitor_devices", lambda: raw)
    monkeypatch.setattr(
        monitors_mod, "_devnode_enabled", lambda devinst: statuses[devinst]
    )

    result = enumerate_monitors()

    assert result == [
        {"instance_id": "DISPLAY\\A\\1", "friendly_name": "Monitor A", "enabled": True},
        {"instance_id": "DISPLAY\\B\\2", "friendly_name": "Monitor B", "enabled": False},
    ]


def test_enumerate_monitors_omits_undetermined_device(monkeypatch):
    raw = [
        ("DISPLAY\\A\\1", "Monitor A", 1001),
        ("DISPLAY\\B\\2", "Monitor B", 1002),
        ("DISPLAY\\C\\3", "Monitor C", 1003),
    ]

    def fake_query(devinst):
        if devinst == 1002:
            raise OSError("CM_Get_DevNode_Status failed")
        return True

    monkeypatch.setattr(monitors_mod, "_list_raw_monitor_devices", lambda: raw)
    monkeypatch.setattr(monitors_mod, "_devnode_enabled", fake_query)

    result = enumerate_monitors()

    ids = [m["instance_id"] for m in result]
    assert ids == ["DISPLAY\\A\\1", "DISPLAY\\C\\3"]
    assert all(m["enabled"] is True for m in result)


# ---------------------------------------------------------------------------
# T2.4 read_op_result(path, app_dir)
# ---------------------------------------------------------------------------


def test_read_op_result_valid_file_returns_dict_and_deletes_it(tmp_path):
    app_dir = str(tmp_path)
    path = os.path.join(app_dir, "monitor_op_result_1234_5678.json")
    payload = {"results": [{"instance_id": "DISPLAY\\A\\1", "ok": True, "message": "Disabled"}]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    result = read_op_result(path, app_dir)

    assert result == payload
    assert not os.path.exists(path)


def test_read_op_result_unsafe_path_returns_none_and_leaves_file(tmp_path):
    app_dir = str(tmp_path)
    outside_dir = tmp_path.parent / "outside_read_op_result"
    outside_dir.mkdir(exist_ok=True)
    path = str(outside_dir / "monitor_op_result_1234_5678.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"results": []}, f)

    result = read_op_result(path, app_dir)

    assert result is None
    assert os.path.exists(path)


def test_read_op_result_malformed_json_returns_none(tmp_path):
    app_dir = str(tmp_path)
    path = os.path.join(app_dir, "monitor_op_result_1234_5678.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    result = read_op_result(path, app_dir)

    assert result is None


def test_read_op_result_malformed_json_logs_before_deleting(tmp_path, monkeypatch):
    # 1h: a corrupted/truncated helper result file must leave a trace in
    # quickres.log instead of vanishing silently -- log before the finally
    # block deletes the file.
    logged = []
    monkeypatch.setattr(monitors_mod, "log_msg", lambda msg: logged.append(msg))
    app_dir = str(tmp_path)
    path = os.path.join(app_dir, "monitor_op_result_1234_5678.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    result = read_op_result(path, app_dir)

    assert result is None
    assert not os.path.exists(path)
    assert len(logged) == 1
    assert "monitor_op_result_1234_5678.json" in logged[0]


# ---------------------------------------------------------------------------
# T2.3 set_monitors_enabled(instance_ids, enabled, ...) -- uniform N=1..N
# ---------------------------------------------------------------------------


def _stub_success_run(monkeypatch, results_by_id, device_states=None):
    monkeypatch.setattr(monitors_mod, "_wait_for_helper", lambda handle, timeout_s: True)
    monkeypatch.setattr(
        monitors_mod,
        "read_op_result",
        lambda path, app_dir: {
            "results": [
                {"instance_id": iid, "ok": ok, "message": message}
                for iid, (ok, message) in results_by_id.items()
            ]
        },
    )
    monkeypatch.setattr(
        monitors_mod,
        "sample_device_states",
        lambda ids: device_states or {iid: None for iid in ids},
    )


def test_set_monitors_enabled_n1_and_n3_same_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")

    _stub_success_run(monkeypatch, {"A": (True, "Disabled")})
    result_n1 = set_monitors_enabled(["A"], False, app_dir=str(tmp_path))
    assert result_n1 == [("A", True, "Disabled", monitors_mod.OUTCOME_CONFIRMED)]

    _stub_success_run(
        monkeypatch,
        {"A": (True, "Disabled"), "B": (True, "Disabled"), "C": (True, "Disabled")},
    )
    result_n3 = set_monitors_enabled(["A", "B", "C"], False, app_dir=str(tmp_path))
    assert result_n3 == [
        ("A", True, "Disabled", monitors_mod.OUTCOME_CONFIRMED),
        ("B", True, "Disabled", monitors_mod.OUTCOME_CONFIRMED),
        ("C", True, "Disabled", monitors_mod.OUTCOME_CONFIRMED),
    ]
    assert all(isinstance(r, tuple) and len(r) == 4 for r in result_n1 + result_n3)


def test_set_monitors_enabled_honors_caller_supplied_result_path(monkeypatch, tmp_path):
    # T3.3: bridge.py pre-computes the result-file path so it can persist a
    # matching REC-1 pending record BEFORE elevation starts; set_monitors_enabled
    # must use that exact path instead of generating its own.
    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")
    monkeypatch.setattr(monitors_mod, "_wait_for_helper", lambda handle, timeout_s: True)

    seen_paths = []

    def _fake_read_op_result(path, app_dir):
        seen_paths.append(path)
        return {"results": [{"instance_id": "A", "ok": True, "message": "Disabled"}]}

    monkeypatch.setattr(monitors_mod, "read_op_result", _fake_read_op_result)
    monkeypatch.setattr(monitors_mod, "sample_device_states", lambda ids: {iid: None for iid in ids})

    caller_path = str(tmp_path / "monitor_op_result_123_456.json")
    result = set_monitors_enabled(["A"], False, app_dir=str(tmp_path), result_path=caller_path)

    assert seen_paths == [caller_path]
    assert result == [("A", True, "Disabled", monitors_mod.OUTCOME_CONFIRMED)]


def test_set_monitors_enabled_unsafe_result_path_raises_before_elevation(monkeypatch, tmp_path):
    # Round-3 finding: instance_ids are validated against the injection
    # allowlist right before _build_helper_params runs, but the
    # caller-supplied result_path was interpolated into the same unescaped
    # command line with no validation at all. Mirrors the read-side check
    # (recovery.is_safe_result_path, used by read_op_result) and the
    # elevated helper's own write-side check (main.py).
    launch_calls = []
    monkeypatch.setattr(
        monitors_mod, "_launch_elevated_helper", lambda params: launch_calls.append(params)
    )
    unsafe_path = str(tmp_path / "not_a_result_file.json")

    with pytest.raises(ValueError):
        set_monitors_enabled(["A"], False, app_dir=str(tmp_path), result_path=unsafe_path)

    assert launch_calls == []


def test_set_monitors_enabled_result_path_outside_app_dir_raises(monkeypatch, tmp_path):
    launch_calls = []
    monkeypatch.setattr(
        monitors_mod, "_launch_elevated_helper", lambda params: launch_calls.append(params)
    )
    outside_dir = tmp_path.parent / "outside_set_monitors_enabled"
    outside_dir.mkdir(exist_ok=True)
    unsafe_path = str(outside_dir / "monitor_op_result_1_2.json")

    with pytest.raises(ValueError):
        set_monitors_enabled(["A"], False, app_dir=str(tmp_path), result_path=unsafe_path)

    assert launch_calls == []


def test_set_monitors_enabled_unsafe_id_aborts_before_elevation(monkeypatch, tmp_path):
    launch_calls = []
    monkeypatch.setattr(
        monitors_mod, "_launch_elevated_helper", lambda params: launch_calls.append(params)
    )

    result = set_monitors_enabled(["A", "bad id!", "C"], False, app_dir=str(tmp_path))

    assert launch_calls == []
    assert len(result) == 3
    assert all(ok is False for (_id, ok, _msg, _kind) in result)
    assert all(kind == monitors_mod.OUTCOME_GENUINE_FAILURE for (_id, _ok, _msg, kind) in result)


def test_set_monitors_enabled_helper_ok_disagrees_with_observed_state_is_unconfirmed(
    monkeypatch, tmp_path
):
    # Round 8 finding 1 (MON-7): a driver's CM_Disable_DevNode call can
    # return CR_SUCCESS without the device's actual state changing. When the
    # helper reports ok=True but the freshly observed device state (from
    # sample_device_states) disagrees with the requested `enabled` value,
    # set_monitors_enabled must NOT report ok=True -- that false-positive
    # would let a caller clear the crash-recovery record for a monitor that
    # is, in truth, still on.
    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")
    _stub_success_run(
        monkeypatch,
        {"A": (True, "Disabled")},
        device_states={"A": True},  # still enabled -- disagrees with disable request
    )

    result = set_monitors_enabled(["A"], False, app_dir=str(tmp_path))

    assert len(result) == 1
    instance_id, ok, message, kind = result[0]
    assert instance_id == "A"
    assert ok is False
    assert message != "Disabled"
    assert kind == monitors_mod.OUTCOME_AMBIGUOUS


def test_set_monitors_enabled_helper_ok_agrees_with_observed_state_stays_ok(monkeypatch, tmp_path):
    # Sanity: when both signals AGREE, the existing confirmed path is unchanged.
    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")
    _stub_success_run(
        monkeypatch,
        {"A": (True, "Disabled")},
        device_states={"A": False},  # actually disabled, agrees
    )

    result = set_monitors_enabled(["A"], False, app_dir=str(tmp_path))

    assert result == [("A", True, "Disabled", monitors_mod.OUTCOME_CONFIRMED)]


def test_set_monitors_enabled_helper_ok_with_undetermined_observed_state_still_trusted(
    monkeypatch, tmp_path
):
    # When observed_state can't be determined (None), there is nothing to
    # cross-check against, so the helper's own report is still trusted --
    # matches the "when BOTH are available" scope of the cross-check.
    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")
    _stub_success_run(
        monkeypatch,
        {"A": (True, "Disabled")},
        device_states={"A": None},
    )

    result = set_monitors_enabled(["A"], False, app_dir=str(tmp_path))

    assert result == [("A", True, "Disabled", monitors_mod.OUTCOME_CONFIRMED)]


def test_set_monitors_enabled_helper_completed_no_result_and_state_unconfirmed_is_ambiguous(
    monkeypatch, tmp_path
):
    # Round 13 R4 finding: the elevated helper can exit within the timeout
    # (so this isn't a TIMEOUT_MESSAGE case) yet still fail to persist its
    # own result file -- e.g. write_json_atomic failing under disk-full or
    # permission-denied inside the elevated process, after its
    # CM_Disable_DevNode call may have already genuinely succeeded. When the
    # fresh device-state re-check (sample_device_states) also can't
    # determine the device's current state, the true outcome is unknown,
    # not a confirmed failure, and must get its own distinct message rather
    # than the generic "Elevated helper did not report a result" text so
    # downstream callers (webview/bridge.py's _finalize_disable_outcome) can
    # tell it apart from a genuine failure the same way they already do for
    # TIMEOUT_MESSAGE.
    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")
    monkeypatch.setattr(monitors_mod, "_wait_for_helper", lambda handle, timeout_s: True)
    monkeypatch.setattr(monitors_mod, "read_op_result", lambda path, app_dir: None)
    monkeypatch.setattr(
        monitors_mod, "sample_device_states", lambda ids: {iid: None for iid in ids}
    )

    result = set_monitors_enabled(["A"], False, app_dir=str(tmp_path))

    assert len(result) == 1
    instance_id, ok, message, kind = result[0]
    assert instance_id == "A"
    assert ok is False
    assert message == monitors_mod.HELPER_RESULT_UNCONFIRMED_MESSAGE
    assert message != monitors_mod.TIMEOUT_MESSAGE
    assert kind == monitors_mod.OUTCOME_AMBIGUOUS


def test_set_monitors_enabled_helper_completed_no_result_but_state_confirmed_is_genuine_failure(
    monkeypatch, tmp_path
):
    # Contrast case for the fix above: when the device state CAN be sampled
    # and it confirms the device is still in its pre-op state (still
    # enabled, for a disable request), that is a genuine confirmed failure
    # -- not ambiguous -- even though the helper itself never reported a
    # result for this id. Only the "device state also unconfirmed" branch
    # should become HELPER_RESULT_UNCONFIRMED_MESSAGE.
    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")
    monkeypatch.setattr(monitors_mod, "_wait_for_helper", lambda handle, timeout_s: True)
    monkeypatch.setattr(monitors_mod, "read_op_result", lambda path, app_dir: None)
    monkeypatch.setattr(monitors_mod, "sample_device_states", lambda ids: {"A": True})

    result = set_monitors_enabled(["A"], False, app_dir=str(tmp_path))

    assert len(result) == 1
    instance_id, ok, message, kind = result[0]
    assert instance_id == "A"
    assert ok is False
    assert message != monitors_mod.HELPER_RESULT_UNCONFIRMED_MESSAGE
    assert message != monitors_mod.TIMEOUT_MESSAGE
    assert kind == monitors_mod.OUTCOME_GENUINE_FAILURE


def test_set_monitors_enabled_helper_not_ok_agrees_with_observed_state_is_ambiguous(
    monkeypatch, tmp_path
):
    # Mirror of the helper_ok=True cross-check above: a helper-reported
    # failure can itself be spurious (a transient CfgMgr32 quirk, or the
    # disable taking effect a moment after the helper's own error return)
    # while the freshly observed device state actually shows the requested
    # `enabled` value was reached. Trusting the helper's False report
    # unconditionally here would report OUTCOME_GENUINE_FAILURE for a
    # monitor that is, in truth, already in the requested state -- which
    # downstream (bridge.py's _finalize_disable_outcome) deletes the
    # on-disk crash-recovery record for and never arms an auto-revert guard
    # for. This must downgrade to the same unconfirmed/ambiguous treatment
    # the reverse mismatch already gets.
    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")
    _stub_success_run(
        monkeypatch,
        {"A": (False, "Access denied")},
        device_states={"A": False},  # actually disabled -- agrees with the disable request
    )

    result = set_monitors_enabled(["A"], False, app_dir=str(tmp_path))

    assert len(result) == 1
    instance_id, ok, message, kind = result[0]
    assert instance_id == "A"
    assert ok is False
    assert message != "Access denied"
    assert kind == monitors_mod.OUTCOME_AMBIGUOUS


@pytest.mark.parametrize(
    "device_states",
    [{"A": True}, {"A": None}],
    ids=["disagreeing_still_enabled", "unavailable"],
)
def test_set_monitors_enabled_helper_not_ok_without_agreement_stays_genuine_failure(
    monkeypatch, tmp_path, device_states
):
    # Regression coverage for the real-failure case: when the observed
    # device state genuinely disagrees with the helper's failure report
    # (device is still enabled) or is simply unavailable to cross-check
    # against, the helper's False report is trusted as-is and stays
    # OUTCOME_GENUINE_FAILURE, matching the existing behavior before this
    # cross-check.
    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")
    _stub_success_run(
        monkeypatch,
        {"A": (False, "Access denied")},
        device_states=device_states,
    )

    result = set_monitors_enabled(["A"], False, app_dir=str(tmp_path))

    assert result == [("A", False, "Access denied", monitors_mod.OUTCOME_GENUINE_FAILURE)]


def test_set_monitors_enabled_one_device_fails_others_succeed(monkeypatch, tmp_path):
    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")
    _stub_success_run(
        monkeypatch,
        {
            "A": (True, "Disabled"),
            "B": (False, "Access denied"),
            "C": (True, "Disabled"),
        },
    )

    result = set_monitors_enabled(["A", "B", "C"], False, app_dir=str(tmp_path))

    assert result == [
        ("A", True, "Disabled", monitors_mod.OUTCOME_CONFIRMED),
        ("B", False, "Access denied", monitors_mod.OUTCOME_GENUINE_FAILURE),
        ("C", True, "Disabled", monitors_mod.OUTCOME_CONFIRMED),
    ]


def test_set_monitors_enabled_disable_all_no_minimum_enabled_guard(monkeypatch, tmp_path):
    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")
    _stub_success_run(
        monkeypatch,
        {"A": (True, "Disabled"), "B": (True, "Disabled"), "C": (True, "Disabled")},
    )

    result = set_monitors_enabled(["A", "B", "C"], False, app_dir=str(tmp_path))

    assert len(result) == 3
    assert all(ok for (_id, ok, _msg, _kind) in result)


def test_set_monitors_enabled_one_elevation_call_regardless_of_n(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_launch(params):
        calls["n"] += 1
        return "handle"

    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", fake_launch)
    _stub_success_run(monkeypatch, {"A": (True, "Disabled")})
    set_monitors_enabled(["A"], False, app_dir=str(tmp_path))
    assert calls["n"] == 1

    calls["n"] = 0
    _stub_success_run(
        monkeypatch,
        {"A": (True, "Disabled"), "B": (True, "Disabled"), "C": (True, "Disabled")},
    )
    set_monitors_enabled(["A", "B", "C"], False, app_dir=str(tmp_path))
    assert calls["n"] == 1


def test_set_monitors_enabled_params_repeat_instance_id_no_batch_flag(monkeypatch, tmp_path):
    captured = {}

    def fake_launch(params):
        captured["params"] = params
        return "handle"

    monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", fake_launch)
    _stub_success_run(
        monkeypatch,
        {"A": (True, "Enabled"), "B": (True, "Enabled"), "C": (True, "Enabled")},
    )

    set_monitors_enabled(["A", "B", "C"], True, app_dir=str(tmp_path))

    params = captured["params"]
    assert "--batch" not in params
    assert params.count("--instance-id") == 3
    assert "--monitor-op enable" in params


# ---------------------------------------------------------------------------
# Round-2 dedup: one shared result-filename generator, used everywhere the
# `monitor_op_result_<pid>_<ms>.json` shape was previously an independent
# literal expression (webview/bridge.py's set_monitors_enabled,
# monitors.py's own set_monitors_enabled, _cleanup_stale_result_files'
# startswith/endswith sweep, recovery.py's validation regex).
# ---------------------------------------------------------------------------


def test_make_result_filename_with_explicit_pid_and_ms():
    assert make_result_filename(pid=111, ms=222) == "monitor_op_result_111_222.json"


def test_make_result_filename_defaults_to_real_pid_and_clock(monkeypatch):
    monkeypatch.setattr(monitors_mod.os, "getpid", lambda: 999)
    monkeypatch.setattr(monitors_mod.time, "time", lambda: 1.234)

    assert make_result_filename() == "monitor_op_result_999_1234.json"


def test_make_result_filename_output_matches_recovery_safe_path_regex():
    name = make_result_filename(pid=1, ms=2)

    assert recovery._RESULT_FILENAME_RE.match(name)


def test_cleanup_stale_result_files_matches_the_shared_naming_convention(tmp_path, monkeypatch):
    monkeypatch.setattr(monitors_mod, "APP_DIR", str(tmp_path))
    # No pending record at all -- isolates this test from whatever real
    # pending_restore.json (if any) happens to exist on the machine running
    # the suite, so the orphan-cleanup path stays deterministic regardless
    # of host disk state.
    monkeypatch.setattr(monitors_mod, "load_pending", lambda: None)
    stale_path = tmp_path / make_result_filename(pid=1, ms=2)
    stale_path.write_text("{}")
    old_time = monitors_mod.time.time() - 3600
    monitors_mod.os.utime(stale_path, (old_time, old_time))

    monitors_mod._cleanup_stale_result_files(max_age_s=1.0)

    assert not stale_path.exists()


def test_cleanup_stale_result_files_keeps_file_still_referenced_by_pending_record(
    tmp_path, monkeypatch
):
    """A stale-by-age result file that the on-disk crash-recovery record
    still references as its unconsumed result_file must survive the sweep
    -- it is the strongest evidence of a still-pending disable/enable
    outcome the recovery ladder has not read yet, and deleting it would
    force a weaker device-state/liveness-only fallback (or UNCONFIRMABLE)
    the next time recover_on_boot/recheck_pending runs for that target.
    """
    monkeypatch.setattr(monitors_mod, "APP_DIR", str(tmp_path))
    referenced_path = tmp_path / make_result_filename(pid=1, ms=2)
    referenced_path.write_text("{}")
    old_time = monitors_mod.time.time() - 3600
    monitors_mod.os.utime(referenced_path, (old_time, old_time))

    monkeypatch.setattr(
        monitors_mod,
        "load_pending",
        lambda: {
            "action": "disable",
            "targets": [{"instance_id": "A", "friendly_name": "A"}],
            "result_file": str(referenced_path),
        },
    )

    monitors_mod._cleanup_stale_result_files(max_age_s=1.0)

    assert referenced_path.exists()


def test_cleanup_stale_result_files_still_removes_genuine_orphan_when_pending_record_exists(
    tmp_path, monkeypatch
):
    """A pending record being present does not blanket-protect every stale
    file in APP_DIR -- only the exact path it references. A stale file that
    belongs to neither an active pending record's result_file (nor to any
    result_file the exercised schema might attach per-target) is a genuine
    orphan and must still be cleaned up, or these accumulate on disk
    indefinitely.
    """
    monkeypatch.setattr(monitors_mod, "APP_DIR", str(tmp_path))
    referenced_path = tmp_path / make_result_filename(pid=1, ms=2)
    referenced_path.write_text("{}")
    orphan_path = tmp_path / make_result_filename(pid=3, ms=4)
    orphan_path.write_text("{}")
    old_time = monitors_mod.time.time() - 3600
    monitors_mod.os.utime(referenced_path, (old_time, old_time))
    monitors_mod.os.utime(orphan_path, (old_time, old_time))

    monkeypatch.setattr(
        monitors_mod,
        "load_pending",
        lambda: {
            "action": "disable",
            "targets": [{"instance_id": "A", "friendly_name": "A"}],
            "result_file": str(referenced_path),
        },
    )

    monitors_mod._cleanup_stale_result_files(max_age_s=1.0)

    assert referenced_path.exists()
    assert not orphan_path.exists()

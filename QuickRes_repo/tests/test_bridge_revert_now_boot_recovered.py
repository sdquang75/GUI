"""revert_now() with no live PendingDisableGuard object (the boot/crash-
recovered path) used to silently no-op: `target_ids = guard.target_ids if
guard is not None else []` evaluated to an empty list whenever
`self._pending_guard` is None, which is UNCONDITIONALLY the case for every
pending state surfaced via `recover_on_boot`/`recheck_pending` -- a live
guard object is only ever constructed by `_arm_auto_revert_guard`, called
only from `_finalize_disable_outcome` inside the SAME process's own
`set_monitors_enabled` disable call. With an empty `target_ids`, the elevated
CM_Enable_DevNode call that would actually re-enable the monitor was never
invoked, yet the `else: config.clear_pending()` branch unconditionally
deleted the on-disk crash-recovery record anyway -- the monitor stayed
physically disabled, the caller was told `{ok: True, results: []}`, and the
record that would let a future `recover_on_boot` re-surface the state for a
retry was gone.

Fix: when `self._pending_guard` is `None`, `revert_now` reads the target ids
from the ON-DISK record (the same `record["targets"]` source
`_resolve_pending_now` already reads) instead of treating "no live guard" as
"nothing to revert", and only trims/clears entries that actually succeeded.
"""
import time

import pytest

from quickres.webview.bridge import Api
from quickres import config, monitors


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


def _write_boot_recovered_record(targets):
    """Simulate the on-disk state `recover_on_boot`/`recheck_pending`
    surface: a real pending_restore.json record with NO live
    PendingDisableGuard object backing it in this process (this is what
    every boot/crash-recovered pending state looks like -- see this file's
    module docstring).

    `started_at` is set well past `recovery.resolve_pending`'s 120s expiry
    window (not `0.0` -- `normalize_pending` falls back to the record's own
    file mtime, effectively "now", whenever `started_at` is falsy, so a
    literal `0.0` here would NOT actually represent a stale/expired record
    and would instead resolve to `Resolution.IN_FLIGHT`). This fixture
    represents the genuinely-expired, helper-is-gone case these tests are
    about -- round-19 R3's `revert_now`/`keep_disabled` IN_FLIGHT guard
    (tests/test_bridge_revert_keep_disabled_in_flight_guard.py) covers the
    still-IN_FLIGHT case separately.
    """
    config.save_pending({
        "action": "disable",
        "targets": [{"instance_id": iid, "friendly_name": name} for iid, name in targets],
        "result_file": None,
        "helper_pid": None,
        "owner_pid": 999999,
        "started_at": time.time() - 300.0,
        "unlocked_at": None,
    })


class TestRevertNowWithNoLiveGuardReadsTargetsFromDisk:
    def test_calls_set_monitors_enabled_with_the_on_disk_target_ids_not_empty(self, monkeypatch):
        _write_boot_recovered_record([("DISPLAY\\A\\1", "A")])
        seen_calls = []

        def _fake_set_monitors_enabled(ids, enabled, **kwargs):
            seen_calls.append((list(ids), enabled))
            return [(iid, True, "Monitor enabled successfully", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fake_set_monitors_enabled
        )
        api = Api()
        assert api._pending_guard is None, "the boot/crash-recovered path never has a live guard"

        result = api.revert_now()

        assert result["ok"] is True
        assert seen_calls == [(["DISPLAY\\A\\1"], True)], (
            "revert_now must actually invoke the elevated re-enable call with "
            "the on-disk record's real target ids, not silently no-op against "
            "an empty list"
        )
        assert result["data"]["results"] == [
            ("DISPLAY\\A\\1", True, "Monitor enabled successfully", monitors.OUTCOME_CONFIRMED)
        ]

    def test_success_clears_the_on_disk_record(self, monkeypatch):
        _write_boot_recovered_record([("DISPLAY\\A\\1", "A")])
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids
            ],
        )
        api = Api()

        result = api.revert_now()

        assert result["ok"] is True
        assert config.load_pending() is None

    def test_failure_to_reenable_does_not_silently_wipe_the_on_disk_record(self, monkeypatch):
        # The exact scenario the finding calls out: a genuine failure to
        # re-enable must never be reported as success by destroying the
        # crash-recovery record anyway -- the target's entry must survive so
        # a future recover_on_boot/recheck_pending can still surface it.
        _write_boot_recovered_record([("DISPLAY\\A\\1", "A")])
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, "Failed to enable monitor (CONFIGRET error 42)",
                 monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ],
        )
        api = Api()

        result = api.revert_now()

        assert result["ok"] is True
        record = config.load_pending()
        assert record is not None, (
            "a genuine failure to re-enable must not silently destroy the "
            "on-disk crash-recovery record -- the monitor is still disabled"
        )
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}

    def test_partial_failure_across_two_targets_keeps_only_the_failed_one(self, monkeypatch):
        _write_boot_recovered_record([("DISPLAY\\A\\1", "A"), ("DISPLAY\\B\\2", "B")])

        def _fake_reenable(ids, enabled, **kwargs):
            return [
                ("DISPLAY\\A\\1", True, "ok", monitors.OUTCOME_CONFIRMED),
                ("DISPLAY\\B\\2", False, "still disabled", monitors.OUTCOME_GENUINE_FAILURE),
            ]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fake_reenable
        )
        api = Api()

        result = api.revert_now()

        assert result["ok"] is True
        record = config.load_pending()
        assert record is not None
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\B\\2"}

    def test_no_guard_and_no_on_disk_record_is_a_harmless_noop(self, monkeypatch):
        # Nothing pending at all -- must not raise or invoke set_monitors_enabled.
        called = []
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: called.append(ids) or [],
        )
        api = Api()
        assert config.load_pending() is None

        result = api.revert_now()

        assert result["ok"] is True
        assert called == []
        assert config.load_pending() is None


class TestRevertNowViaTheRealBootArmedRecoveryFlow:
    """End-to-end proof through the actual UI-reachable path: an IN_FLIGHT
    outcome from `recover_on_boot` re-arms `self._op_lock` with
    `self._boot_armed = True` (see `bridge_op`'s `boot_armed_bypass`), and
    `revert_now` is one of the escape hatches that resolves it -- exactly
    the flow the finding describes as unconditionally hitting the
    guard-is-None bug.
    """

    def test_revert_now_after_recover_on_boot_actually_reenables_the_monitor(self, monkeypatch):
        _write_boot_recovered_record([("DISPLAY\\A\\1", "A")])

        api = Api()
        api._op_lock.acquire(blocking=False)
        api._lock_reason = "A monitor operation from a previous session is still resolving"
        api._boot_armed = True
        assert api._pending_guard is None

        seen_calls = []

        def _fake_set_monitors_enabled(ids, enabled, **kwargs):
            seen_calls.append(list(ids))
            return [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fake_set_monitors_enabled
        )

        result = api.revert_now()

        assert result["ok"] is True
        assert seen_calls == [["DISPLAY\\A\\1"]]
        assert api._op_lock.locked() is False
        assert api._boot_armed is False
        assert config.load_pending() is None

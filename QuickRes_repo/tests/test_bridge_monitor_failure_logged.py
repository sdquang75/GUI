"""Round 15 finding (medium, resilience): monitor enable/disable outcomes
(per-target failures, cancelled/declined UAC prompts, elevated-helper
crashes, and an exhausted auto-revert retry budget) were never written to
quickres.log anywhere in the disable/enable/auto-revert pipeline -- unlike
`pick_resolution` (see tests/test_bridge_pick_resolution_failure_logged.py),
which already leaves a trace in quickres.log instead of vanishing silently.
In a console=False packaged build, quickres.log is the only observability
channel; a user/maintainer investigating a recurring "monitor won't
disable" report had zero log evidence.

Fix: `_finalize_disable_outcome` now logs genuine per-target disable
failures, `set_monitors_enabled`'s enable branch logs genuine per-target
enable failures, and `_maybe_retry_auto_revert` logs when the bounded
auto-revert retry budget is exhausted with the guard still unresolved.
"""
import time

import pytest

from quickres.webview.bridge import Api
from quickres.monitors import PendingDisableGuard
from quickres import config, monitors


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


def _capture_log(monkeypatch):
    logged = []
    monkeypatch.setattr("quickres.webview.bridge.log_msg", lambda msg: logged.append(msg))
    return logged


class TestDisableGenuineFailureLogged:
    def test_finalize_disable_outcome_logs_a_genuine_per_target_failure(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "Monitor A", "enabled": True}],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, "Elevation was cancelled or failed", monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ],
        )
        logged = _capture_log(monkeypatch)
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        assert result["data"]["results"][0][1] is False
        assert len(logged) == 1
        assert "DISPLAY\\A\\1" in logged[0]
        assert "Elevation was cancelled or failed" in logged[0]

    def test_finalize_disable_outcome_does_not_log_a_confirmed_success(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "Monitor A", "enabled": True}],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        logged = _capture_log(monkeypatch)
        api = Api()

        # A confirmed disable arms a REAL 10s threading.Timer (the
        # auto-revert guard, see Api._arm_auto_revert_guard/_arm_guard_timer)
        # since this test never monkeypatches threading.Timer itself. Left
        # uncancelled, that daemon timer outlives this test's own monkeypatch
        # teardown and fires ~10s later against whatever unrelated test
        # happens to be running at that moment -- see the sibling test below
        # (test_maybe_retry_auto_revert_does_not_log_while_budget_remains),
        # which cancels its own guard timer the same way, for the identical
        # reason.
        try:
            result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

            assert result["ok"] is True
            assert logged == []
        finally:
            if api._pending_guard_timer is not None:
                api._pending_guard_timer.cancel()


class TestEnableGenuineFailureLogged:
    def test_enable_branch_logs_a_genuine_per_target_failure(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, "Elevation was cancelled or failed", monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ],
        )
        logged = _capture_log(monkeypatch)
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        assert result["ok"] is True
        assert len(logged) == 1
        assert "DISPLAY\\A\\1" in logged[0]

    def test_enable_branch_does_not_log_a_confirmed_success(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "Enabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        logged = _capture_log(monkeypatch)
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        assert result["ok"] is True
        assert logged == []


class TestAutoRevertBudgetExhaustionLogged:
    def test_maybe_retry_auto_revert_logs_when_budget_is_exhausted(self, monkeypatch):
        from quickres.webview import bridge as bridge_mod

        logged = _capture_log(monkeypatch)
        api = Api()
        guard = PendingDisableGuard(
            armed_at=time.time(), target_ids=["DISPLAY\\A\\1"],
            revert_fn=lambda ids: [
                (iid, False, "still failing", monitors.OUTCOME_GENUINE_FAILURE) for iid in ids
            ],
        )
        api._pending_guard = guard
        api._pending_guard_attempt = bridge_mod._AUTO_REVERT_MAX_ATTEMPTS

        # _maybe_retry_auto_revert requires self._op_lock already held by
        # the caller (see tests/test_bridge_arm_guard_timer_lock_assertion.py) --
        # every real call site reaches it while already holding the lock.
        api._op_lock.acquire()
        try:
            api._maybe_retry_auto_revert(guard)
        finally:
            api._op_lock.release()

        assert len(logged) == 1
        assert "DISPLAY\\A\\1" in logged[0]

    def test_maybe_retry_auto_revert_does_not_log_while_budget_remains(self, monkeypatch):
        logged = _capture_log(monkeypatch)
        api = Api()
        guard = PendingDisableGuard(
            armed_at=time.time(), target_ids=["DISPLAY\\A\\1"],
            revert_fn=lambda ids: [
                (iid, False, "still failing", monitors.OUTCOME_GENUINE_FAILURE) for iid in ids
            ],
        )
        api._pending_guard = guard
        api._pending_guard_attempt = 1

        api._op_lock.acquire()
        try:
            api._maybe_retry_auto_revert(guard)
        finally:
            if api._pending_guard_timer is not None:
                api._pending_guard_timer.cancel()
            api._op_lock.release()

        assert logged == []

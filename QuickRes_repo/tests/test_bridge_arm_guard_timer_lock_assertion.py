"""R2 Readability finding (low), defense-in-depth companion to
`_resolve_pending_now`'s existing `assert self._op_lock.locked()`
(tests/test_bridge_resolve_pending_now_lock_assertion.py): `_arm_guard_timer`
and `_maybe_retry_auto_revert` both mutate/read `self._pending_guard`/
`self._pending_guard_timer` under the exact same implicit "caller must
already hold `self._op_lock`" precondition -- they are reused, just like
`_resolve_pending_now`, from call sites that hold the lock through different
mechanisms (`bridge_op(lock=True)` for `set_monitors_enabled`/`revert_now`,
and `_resolve_guard_unbounded_under_lock`'s own `with _LockAcquireGuard...`
block). Nothing enforced that precondition at runtime for either method; a
future caller reaching either one from an unlocked context would silently
race the timer/guard state the same way an unlocked `_resolve_pending_now`
call would race the on-disk record.
"""
import time

import pytest

from quickres.webview.bridge import Api
from quickres import config
from quickres.monitors import PendingDisableGuard


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


def _guard():
    return PendingDisableGuard(
        armed_at=time.time(), target_ids=["DISPLAY\\A\\1"],
        revert_fn=lambda ids: [(iid, True, "ok") for iid in ids],
    )


class TestArmGuardTimerRequiresTheLockAlreadyHeld:
    def test_raises_assertion_error_when_called_without_the_lock_held(self):
        api = Api()
        assert not api._op_lock.locked()

        with pytest.raises(AssertionError):
            api._arm_guard_timer(_guard(), 10.0)

    def test_works_normally_when_called_with_the_lock_already_held(self):
        api = Api()
        api._op_lock.acquire()
        try:
            api._arm_guard_timer(_guard(), 10.0)
        finally:
            if api._pending_guard_timer is not None:
                api._pending_guard_timer.cancel()
            api._op_lock.release()

        assert api._pending_guard_timer is not None


class TestMaybeRetryAutoRevertRequiresTheLockAlreadyHeld:
    def test_raises_assertion_error_when_called_without_the_lock_held(self):
        api = Api()
        assert not api._op_lock.locked()
        guard = _guard()
        api._pending_guard = guard

        with pytest.raises(AssertionError):
            api._maybe_retry_auto_revert(guard)

    def test_works_normally_when_called_with_the_lock_already_held(self):
        api = Api()
        guard = _guard()
        api._pending_guard = guard
        api._pending_guard_attempt = 1
        api._op_lock.acquire()
        try:
            api._maybe_retry_auto_revert(guard)
        finally:
            if api._pending_guard_timer is not None:
                api._pending_guard_timer.cancel()
            api._op_lock.release()

        assert api._pending_guard_attempt == 2


class TestExistingLockHoldingCallersStillWork:
    def test_set_monitors_enabled_disable_still_arms_the_guard_timer(self, monkeypatch):
        # End-to-end: set_monitors_enabled (bridge_op(lock=True)) reaches
        # _arm_auto_revert_guard -> _arm_guard_timer while genuinely holding
        # self._op_lock -- the new assertion must not disturb this path.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", "confirmed") for iid in ids],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        assert api._pending_guard is not None
        assert api._pending_guard_timer is not None
        api._pending_guard_timer.cancel()

    def test_resolve_guard_unbounded_under_lock_still_retries_via_maybe_retry(self, monkeypatch):
        # _resolve_guard_unbounded_under_lock holds the lock itself (its own
        # _LockAcquireGuard.unbounded), then reaches _maybe_retry_auto_revert
        # on an expired-but-unresolved check -- must still work under the
        # new assertion.
        guard = PendingDisableGuard(
            armed_at=time.time() - 3600, target_ids=["DISPLAY\\A\\1"],
            revert_fn=lambda ids: [(iid, False, "still stuck") for iid in ids],
        )
        api = Api()
        api._pending_guard = guard
        api._pending_guard_attempt = 1

        api._resolve_guard_unbounded_under_lock(guard, now=time.time())

        assert api._pending_guard_attempt == 2
        if api._pending_guard_timer is not None:
            api._pending_guard_timer.cancel()

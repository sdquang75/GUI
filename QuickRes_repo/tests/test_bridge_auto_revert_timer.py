"""1g: PendingDisableGuard.check() is fully implemented and unit-tested in
isolation (test_monitors_guard.py), but nothing in production ever called
it -- the UI's "if you do nothing, QuickRes re-enables it automatically"
copy (i18n.py revert_note) was false. Api.set_monitors_enabled must start a
real threading.Timer matching the guard's own timeout_s that calls
guard.check(time.time()) on expiry, must be safe to fire as a no-op after
keep_disabled/revert_now already confirmed the guard, and must not leak a
timer across multiple disable operations.
"""
import threading
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


class _FakeTimer:
    instances = []

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.started = False
        self.cancelled = False
        _FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


@pytest.fixture(autouse=True)
def _fake_timer(monkeypatch):
    _FakeTimer.instances = []
    monkeypatch.setattr("quickres.webview.bridge.threading.Timer", _FakeTimer)
    yield _FakeTimer
    _FakeTimer.instances = []


def _api_with_confirmed_disable(monkeypatch):
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True}],
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.set_monitors_enabled",
        lambda ids, enabled, **kwargs: [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
    )
    api = Api()
    api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
    return api


class TestTimerIsScheduledOnConfirmedDisable:
    def test_timer_started_matching_guard_timeout(self, monkeypatch):
        api = _api_with_confirmed_disable(monkeypatch)

        assert len(_FakeTimer.instances) == 1
        timer = _FakeTimer.instances[0]
        assert timer.started is True
        assert timer.interval == api._pending_guard.timeout_s

    def test_firing_the_timer_reverts_when_not_confirmed(self, monkeypatch):
        revert_calls = []
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True}],
        )

        def _fake_set_monitors_enabled(ids, enabled, **kwargs):
            if enabled:
                revert_calls.append(ids)
            return [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            _fake_set_monitors_enabled,
        )
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        timer = _FakeTimer.instances[0]
        # Simulate real expiry firing (a real threading.Timer only invokes
        # its function after the full 10s elapses) without an actual 10s
        # test-suite wait: fast-forward the clock the guard's check() reads.
        future = time.time() + 3600
        monkeypatch.setattr("quickres.webview.bridge.time.time", lambda: future)
        timer.function()

        assert revert_calls == [["DISPLAY\\A\\1"]]

    def test_firing_the_timer_after_keep_disabled_confirm_is_a_no_op(self, monkeypatch):
        revert_calls = []
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True}],
        )

        def _fake_set_monitors_enabled(ids, enabled, **kwargs):
            if enabled:
                revert_calls.append(ids)
            return [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            _fake_set_monitors_enabled,
        )
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        api.keep_disabled()

        timer = _FakeTimer.instances[0]
        timer.function()  # already confirmed -- must be a no-op per guard.check()

        assert revert_calls == []

    def test_second_disable_while_unresolved_is_refused_first_timer_survives(self, monkeypatch):
        # Round-3 fix (TestPendingGuardOverwriteProtection): a second
        # disable call while the first guard is still unresolved must never
        # silently cancel/replace the first guard's timer -- it is refused
        # outright instead, regardless of whether it targets the same or a
        # different monitor.
        api = _api_with_confirmed_disable(monkeypatch)
        first_timer = _FakeTimer.instances[0]
        assert first_timer.cancelled is False

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is False
        assert first_timer.cancelled is False
        assert len(_FakeTimer.instances) == 1

    def test_disable_after_guard_confirmed_starts_a_fresh_timer(self, monkeypatch):
        api = _api_with_confirmed_disable(monkeypatch)
        first_timer = _FakeTimer.instances[0]
        api.keep_disabled()  # resolves/confirms + already cancels the first timer
        assert first_timer.cancelled is True

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        assert len(_FakeTimer.instances) == 2


class TestTimerLockSerialization:
    """Round-2 regression fix: the timer callback used to call
    `guard.check()` without ever acquiring `self._op_lock`, so it could
    race `keep_disabled`/`revert_now`/`force_unlock_pending` (which DO hold
    that lock via `bridge_op(lock=True)`) right at the 10s boundary --
    producing a state where `keep_disabled` reports success to the UI while
    the timer silently reverts the device underneath it, with zero trace.
    The fix makes the timer's callback (`Api._resolve_guard_unbounded_under_lock`, also
    reused by webview/app.py's window-close handler) take a real BLOCKING
    acquire of `self._op_lock` before touching the guard, so it can never
    run concurrently with a bridge_op(lock=True)-guarded method's body.
    """

    def _api_with_confirmed_disable(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True}],
        )
        revert_calls = []

        def _fake_set_monitors_enabled(ids, enabled, **kwargs):
            if enabled:
                revert_calls.append(ids)
            return [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            _fake_set_monitors_enabled,
        )
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        return api, revert_calls

    def test_keep_disabled_reports_busy_not_a_false_kept_while_timer_holds_the_lock(
        self, monkeypatch
    ):
        api, revert_calls = self._api_with_confirmed_disable(monkeypatch)
        guard = api._pending_guard

        entered_check = threading.Event()
        release_check = threading.Event()
        real_check = guard.check

        def _paused_check(now):
            entered_check.set()
            release_check.wait(timeout=2)
            return real_check(now)

        monkeypatch.setattr(guard, "check", _paused_check)

        future = time.time() + 3600
        timer_thread = threading.Thread(
            target=lambda: api._resolve_guard_unbounded_under_lock(guard, now=future)
        )
        timer_thread.start()
        assert entered_check.wait(timeout=2), "timer callback never reached guard.check()"

        # self._op_lock is held right now by the timer's still-in-flight
        # check() call -- keep_disabled's single non-blocking acquire
        # attempt MUST see busy, never sneak in and race guard.confirm()
        # against it.
        result = api.keep_disabled()

        release_check.set()
        timer_thread.join(timeout=2)

        assert result == {
            "ok": False, "kind": "busy", "data": None,
            "message": "Another monitor operation is in progress",
        }
        assert revert_calls == [["DISPLAY\\A\\1"]]

    def test_timer_never_runs_guard_check_while_keep_disabled_holds_the_lock(self, monkeypatch):
        api, revert_calls = self._api_with_confirmed_disable(monkeypatch)
        guard = api._pending_guard

        entered_confirm = threading.Event()
        release_confirm = threading.Event()
        real_confirm = guard.confirm

        def _paused_confirm():
            entered_confirm.set()
            release_confirm.wait(timeout=2)
            return real_confirm()

        monkeypatch.setattr(guard, "confirm", _paused_confirm)

        keep_results = []
        keep_thread = threading.Thread(target=lambda: keep_results.append(api.keep_disabled()))
        keep_thread.start()
        assert entered_confirm.wait(timeout=2), "keep_disabled never reached guard.confirm()"

        future = time.time() + 3600
        timer_thread = threading.Thread(
            target=lambda: api._resolve_guard_unbounded_under_lock(guard, now=future)
        )
        timer_thread.start()
        # keep_disabled's confirm() is still paused mid-flight, holding
        # self._op_lock -- give the timer thread a moment to (incorrectly)
        # race in if the lock weren't actually serializing them.
        time.sleep(0.1)
        assert revert_calls == [], (
            "timer ran guard.check() while keep_disabled's critical section "
            "was still in flight -- self._op_lock did not serialize them"
        )

        release_confirm.set()
        keep_thread.join(timeout=2)
        timer_thread.join(timeout=2)

        assert keep_results[0]["ok"] is True
        assert revert_calls == [], "guard was already confirmed -- the timer's check() must no-op, not revert"


class TestPendingRecordClearingTiedToRevertOutcome:
    """Round-3 finding 4 (bridge.py side): _resolve_guard_unbounded_under_lock must not
    clear the on-disk pending_restore.json record unless guard.check()
    actually completed a real revert -- if revert_fn raises (surfaced via
    config.call_logged, which returns None on exception), the pending
    record must survive so recover_on_boot can still surface the unresolved
    state on next launch.
    """

    def _api_with_confirmed_disable(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True}],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        return api

    def test_pending_record_cleared_when_auto_revert_succeeds(self, monkeypatch):
        api = self._api_with_confirmed_disable(monkeypatch)
        assert config.load_pending() is not None
        guard = api._pending_guard

        future = time.time() + 3600
        api._resolve_guard_unbounded_under_lock(guard, now=future)

        assert guard.resolved is True
        assert config.load_pending() is None

    def test_pending_record_survives_when_auto_revert_raises(self, monkeypatch):
        api = self._api_with_confirmed_disable(monkeypatch)
        assert config.load_pending() is not None
        guard = api._pending_guard
        monkeypatch.setattr(
            guard, "_revert_fn", lambda ids: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        future = time.time() + 3600
        api._resolve_guard_unbounded_under_lock(guard, now=future)  # must not raise -- call_logged swallows it

        assert guard.resolved is False
        assert config.load_pending() is not None


class TestPendingGuardOverwriteProtection:
    """Round-3 CRITICAL finding: Api tracks only one self._pending_guard
    globally. Disabling monitor A arms a guard; before it resolves,
    disabling monitor B is a SEPARATE set_monitors_enabled call that CAN
    proceed once A's own lock=True acquire/release cycle has completed (the
    lock is not held across the whole 10s grace period) -- silently
    overwriting self._pending_guard and destroying A's auto-revert
    protection with no trace and no warning.
    """

    def _confirmed_disable(self, monkeypatch, instance_id, friendly_name):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [{"instance_id": instance_id, "friendly_name": friendly_name, "enabled": True}],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )

    def test_second_disable_refuses_while_first_guard_still_unresolved(self, monkeypatch):
        self._confirmed_disable(monkeypatch, "DISPLAY\\A\\1", "A")
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        guard_a = api._pending_guard
        assert guard_a is not None
        assert guard_a.resolved is False

        self._confirmed_disable(monkeypatch, "DISPLAY\\B\\2", "B")
        result = api.set_monitors_enabled(["DISPLAY\\B\\2"], False)

        # Must never silently overwrite: either the call is refused outright
        # (kind="error", never "ok") or A's guard is still the one installed
        # and still unresolved -- never a scenario where B "succeeds" and
        # A's guard has vanished/been replaced.
        assert result["ok"] is False
        assert api._pending_guard is guard_a
        assert api._pending_guard.resolved is False
        assert api._pending_guard.target_ids == ["DISPLAY\\A\\1"]

    def test_second_disable_succeeds_once_first_guard_is_confirmed(self, monkeypatch):
        self._confirmed_disable(monkeypatch, "DISPLAY\\A\\1", "A")
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        api.keep_disabled()  # A's guard is now resolved/confirmed

        self._confirmed_disable(monkeypatch, "DISPLAY\\B\\2", "B")
        result = api.set_monitors_enabled(["DISPLAY\\B\\2"], False)

        assert result["ok"] is True
        assert api._pending_guard is not None
        assert api._pending_guard.target_ids == ["DISPLAY\\B\\2"]

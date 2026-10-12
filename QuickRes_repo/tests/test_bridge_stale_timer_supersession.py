"""Round-31 HIGH finding: `threading.Timer.cancel()` is a no-op once the
real timer thread has already passed CPython's one-time
`finished.is_set()` check inside `Timer.run()` and started calling its
function -- cancelling it after that point (e.g. because the timer's
callback is merely blocked trying to acquire `self._op_lock`, which looks
"not yet fired" from the outside) does nothing. A superseded auto-revert
retry cycle could therefore still resume once it finally gets the lock and
fire a second real `revert_fn` call (a second unrequested elevated-helper
launch) plus a second `_maybe_retry_auto_revert` call for the same logical
retry cycle, arming a third timer.

`_arm_guard_timer` now threads the exact `threading.Timer` instance it
creates through to `_resolve_guard_unbounded_under_lock` as `source_timer`.
Once that call finally acquires `self._op_lock`, it first checks whether
`self._pending_guard_timer` is still that exact object -- if some other
caller has since cancelled-and-replaced it (a fresh retry timer) or torn it
down entirely (guard fully resolved), this stale invocation is a no-op
instead of re-running `guard.check()`.
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


def _guard():
    return monitors.PendingDisableGuard(
        armed_at=time.time(), target_ids=["DISPLAY\\A\\1"],
        revert_fn=lambda ids: [(iid, False, "still stuck", monitors.OUTCOME_GENUINE_FAILURE) for iid in ids],
    )


class TestResolveGuardSourceTimerStaleness:
    def test_stale_source_timer_is_a_no_op(self, monkeypatch):
        """Directly exercises the new staleness gate: the timer that is
        passed in as `source_timer` is no longer `self._pending_guard_timer`
        (something else superseded it) -- guard.check() must never run."""
        api = Api()
        guard = _guard()
        api._pending_guard = guard
        checked = []
        monkeypatch.setattr(guard, "check", lambda now: checked.append(now) or True)

        stale_timer = object()
        current_timer = object()
        api._pending_guard_timer = current_timer  # something else is now current

        api._resolve_guard_unbounded_under_lock(guard, now=time.time() + 3600, source_timer=stale_timer)

        assert checked == [], "a stale source_timer must never reach guard.check()"

    def test_matching_source_timer_still_proceeds(self, monkeypatch):
        """Regression: when the passed-in source_timer IS still the current
        one (the normal, non-superseded firing), behavior is unchanged."""
        api = Api()
        guard = _guard()
        api._pending_guard = guard
        checked = []
        monkeypatch.setattr(guard, "check", lambda now: checked.append(now) or False)

        current_timer = object()
        api._pending_guard_timer = current_timer

        # Not fast-forwarded: guard.check() is monkeypatched directly below,
        # so the real expiry value doesn't matter for what this test checks
        # (whether check() ran at all) -- keeping `now` un-expired just
        # avoids also exercising the separate _maybe_retry_auto_revert /
        # _arm_guard_timer retry-arm path, which is out of scope here and
        # would otherwise try to `.cancel()` the plain `object()` stand-ins
        # used for `current_timer` above.
        api._resolve_guard_unbounded_under_lock(guard, now=guard._armed_at, source_timer=current_timer)

        assert len(checked) == 1

    def test_no_source_timer_still_proceeds_like_before(self, monkeypatch):
        """Non-timer callers (webview/app.py's window-close handler,
        confirm_update's background resolver) never pass source_timer --
        must keep behaving exactly as before this fix."""
        api = Api()
        guard = _guard()
        api._pending_guard = guard
        checked = []
        monkeypatch.setattr(guard, "check", lambda now: checked.append(now) or False)
        api._pending_guard_timer = object()

        api._resolve_guard_unbounded_under_lock(guard, now=guard._armed_at)

        assert len(checked) == 1


class TestArmGuardTimerPassesItselfAsSourceTimer:
    def test_timer_callback_threads_the_real_timer_instance_through(self, monkeypatch):
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
        guard = api._pending_guard
        timer = _FakeTimer.instances[0]

        seen = {}

        def _fake_resolve(g, now=None, source_timer=None):
            seen["guard"] = g
            seen["source_timer"] = source_timer

        monkeypatch.setattr(api, "_resolve_guard_unbounded_under_lock", _fake_resolve)

        timer.function()

        assert seen["guard"] is guard
        assert seen["source_timer"] is timer


class TestStaleTimerSurvivingCancelDoesNotDoubleRevert:
    """End-to-end reproduction of the refuter-verified race: T1 fires,
    `revert_now` wins the lock first, reports a partial failure, and arms a
    fresh retry timer T2 -- cancelling T1 (a no-op, since T1 already
    "fired" as far as the production code can tell). T1's callback must
    still be a no-op once it (belatedly) gets the lock.
    """

    def test_stale_t1_does_not_re_revert_or_arm_a_third_timer(self, monkeypatch):
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
        t1 = _FakeTimer.instances[0]
        assert t1.cancelled is False

        # Fast-forward the clock the guard's own expiry check reads, so T1's
        # eventual (stale) callback would see the guard as expired -- exactly
        # what makes the race dangerous in production.
        future = time.time() + 3600
        monkeypatch.setattr("quickres.webview.bridge.time.time", lambda: future)

        revert_calls = []

        def _partial_fail(ids, enabled, **kwargs):
            revert_calls.append(list(ids))
            return [(iid, False, "still stuck", monitors.OUTCOME_GENUINE_FAILURE) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _partial_fail,
        )

        result = api.revert_now()

        assert result["ok"] is True
        assert t1.cancelled is True  # revert_now's own upfront cancel() call
        assert len(_FakeTimer.instances) == 2, "revert_now's partial-failure retry must arm T2"
        t2 = _FakeTimer.instances[1]
        assert api._pending_guard_timer is t2
        attempt_after_revert_now = api._pending_guard_attempt
        assert attempt_after_revert_now == 2
        assert revert_calls == [["DISPLAY\\A\\1"]]

        # T1's real thread had already passed the point where cancel() can
        # stop it, and only now gets self._op_lock.
        t1.function()

        assert revert_calls == [["DISPLAY\\A\\1"]], "stale T1 must not fire a second real revert"
        assert api._pending_guard_attempt == attempt_after_revert_now, "stale T1 must not arm a third timer"
        assert len(_FakeTimer.instances) == 2, "stale T1 must not arm a third timer"

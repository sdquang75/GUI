"""Round 15 (Resilience finding, HIGH): the bounded auto-revert retry budget
(_AUTO_REVERT_MAX_ATTEMPTS, see test_bridge_auto_revert_retry.py) was silently
defeated whenever a revert attempt raised an exception instead of returning a
normal ok=False result.

`_resolve_guard_unbounded_under_lock` reads `triggered = config.call_logged(guard.check,
now, ...)`. `call_logged` swallows any exception raised by `guard.check` (and
therefore by `revert_fn` underneath it) and returns None -- so `triggered` is
falsy whenever the revert attempt itself raised. `_maybe_retry_auto_revert`
(the only thing that re-arms the guard's timer for another attempt) used to
be called ONLY inside `if triggered:`, so a raised exception on the very
first auto-revert attempt meant attempts 2 and 3 (the existing bounded-retry
budget) never got scheduled at all -- the pending record survived but nothing
further happened automatically, silently defeating the round 12 retry fix for
exactly the failure mode (an unreliable elevated helper / Win32 call chain)
that fix exists for.

Fix: a retry must be considered whenever the guard remains unresolved after
an expired `check()` call, regardless of whether that call returned a
falsy/truthy result normally or only reached that state because
`revert_fn` raised and `call_logged` swallowed it.
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


class _AdvancingClock:
    """A `time.time` stand-in that moves forward by a large `step` on every
    read, unlike a constant `lambda: future`. `_arm_guard_timer` now reads
    the real clock via `guard.rearm(now=time.time(), ...)` every time it
    (re-)arms a timer (Round 32 fix), so a firing that reads the SAME
    constant timestamp both when a retry is armed and when that retry
    later fires would see zero elapsed time and never appear expired. This
    keeps every simulated firing arbitrarily far past whatever deadline was
    most recently armed, matching what these tests actually intend to
    simulate: an attempt that fires long after it was scheduled.
    """

    def __init__(self, start, step=10000.0):
        self._t = start
        self._step = step

    def __call__(self):
        self._t += self._step
        return self._t


def _api_with_confirmed_disable(monkeypatch, revert_fn):
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True}],
    )
    calls = {"disable": 0}

    def _set_monitors_enabled(ids, enabled, **kwargs):
        if not enabled:
            calls["disable"] += 1
            return [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids]
        return revert_fn(ids)

    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.set_monitors_enabled", _set_monitors_enabled
    )
    api = Api()
    api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
    return api


class TestRetryStillScheduledWhenRevertFnRaises:
    def test_first_attempt_raising_still_arms_a_retry_timer(self, monkeypatch):
        revert_calls = []

        def _always_raises(ids):
            revert_calls.append(ids)
            raise RuntimeError("elevated helper crashed")

        api = _api_with_confirmed_disable(monkeypatch, _always_raises)
        guard = api._pending_guard
        assert len(_FakeTimer.instances) == 1
        assert api._pending_guard_attempt == 1

        first_timer = _FakeTimer.instances[0]
        future = time.time() + 3600
        monkeypatch.setattr("quickres.webview.bridge.time.time", lambda: future)
        first_timer.function()

        assert revert_calls == [["DISPLAY\\A\\1"]]
        assert guard.resolved is False, "an exception must not be reported as resolved"
        assert api._pending_guard is guard, "the same guard must stay armed for a retry"
        assert api._pending_guard_attempt == 2, (
            "a raised exception must count as an unresolved attempt eligible "
            "for the same bounded retry budget as a normal ok=False failure"
        )
        assert len(_FakeTimer.instances) == 2, (
            "a retry timer must be armed after an attempt that raised, exactly "
            "like the existing behavior for a normal ok=False failure"
        )
        retry_timer = _FakeTimer.instances[1]
        assert retry_timer.started is True
        from quickres.webview import bridge as bridge_mod
        assert retry_timer.interval == bridge_mod._AUTO_REVERT_RETRY_DELAY_S
        # On-disk crash-recovery record must survive an unresolved attempt.
        assert config.load_pending() is not None

    def test_exhausting_the_budget_via_raises_stops_retrying(self, monkeypatch):
        from quickres.webview import bridge as bridge_mod

        revert_calls = []

        def _always_raises(ids):
            revert_calls.append(ids)
            raise RuntimeError("elevated helper crashed")

        api = _api_with_confirmed_disable(monkeypatch, _always_raises)
        guard = api._pending_guard

        monkeypatch.setattr(
            "quickres.webview.bridge.time.time", _AdvancingClock(time.time() + 3600)
        )

        for _ in range(bridge_mod._AUTO_REVERT_MAX_ATTEMPTS):
            _FakeTimer.instances[-1].function()

        assert len(revert_calls) == bridge_mod._AUTO_REVERT_MAX_ATTEMPTS
        assert guard.resolved is False
        assert len(_FakeTimer.instances) == bridge_mod._AUTO_REVERT_MAX_ATTEMPTS, (
            "no further retry timer may be armed once the attempt budget is "
            "exhausted, even when every attempt failed via a raised exception"
        )
        assert config.load_pending() is not None


class TestTimerCallbackWrapsFullResolutionInCallLogged:
    """Round 17 finding 2(b) (R4 Resilience, HIGH): app.py's window-close
    handler (`_on_closing` in webview/app.py) already wraps its entire
    `api._resolve_guard_unbounded_under_lock(guard)` call in
    `config.call_logged`. The production auto-revert `threading.Timer`
    armed by `_arm_guard_timer` did not -- it called
    `self._resolve_guard_unbounded_under_lock(guard)` directly, and inside
    that method only `guard.check()` itself is shielded via `call_logged`.
    The following `self._clear_or_trim_pending_record(...)` call (reached
    whenever `check()` actually attempted a revert) ran completely
    unguarded on the timer's own daemon thread -- an exception raised there
    (e.g. `_remove_targets_from_pending` hitting a malformed on-disk
    record) had nothing above it to catch it, so it vanished into Python's
    default `threading.excepthook`, unreachable in the console=False
    packaged build, leaving the guard permanently unresolved with no log
    trace explaining why.
    """

    def test_exception_after_guard_check_is_logged_not_raised_from_the_real_timer_callback(
        self, monkeypatch
    ):
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

        # Fails AFTER guard.check() (which already has its own call_logged
        # shield) has already succeeded -- exercising the previously
        # unguarded remainder of _resolve_guard_unbounded_under_lock, not
        # the guard.check()-raises case test_first_attempt_raising_still_arms_a_retry_timer
        # above already covers.
        monkeypatch.setattr(
            api, "_clear_or_trim_pending_record",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk exploded")),
        )

        logged = []
        monkeypatch.setattr("quickres.config.log_msg", lambda msg: logged.append(msg))

        future = time.time() + 3600
        monkeypatch.setattr("quickres.webview.bridge.time.time", lambda: future)

        # The REAL Timer callback captured by _arm_guard_timer -- not a
        # direct call to _resolve_guard_unbounded_under_lock -- so this only
        # passes once the Timer's own lambda wraps the FULL call in
        # config.call_logged, not just guard.check() inside it.
        timer = _FakeTimer.instances[0]
        timer.function()  # must not raise

        assert any("disk exploded" in msg for msg in logged), (
            "an exception raised anywhere in _resolve_guard_unbounded_under_lock's "
            "full execution (not just inside guard.check()) must be logged via "
            "log_msg when reached through the real production Timer callback, "
            f"got: {logged}"
        )

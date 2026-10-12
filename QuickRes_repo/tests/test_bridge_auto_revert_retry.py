"""Round 12 (Resilience finding): the 10s auto-revert "safety net" for a
disabled monitor executes its revert through monitors.set_monitors_enabled,
which ALWAYS routes through a fresh interactive UAC prompt with no cached
elevation. If the user can't see/answer that prompt (the black-screen
scenario the feature exists for), the one-shot timer that used to exist
here would never try again, leaving the monitor disabled indefinitely.

The fix bounds automatic retries at _AUTO_REVERT_MAX_ATTEMPTS: after a
triggered-but-incomplete revert attempt (timeout or genuine failure), the
guard re-arms one more timer (at _AUTO_REVERT_RETRY_DELAY_S) instead of
giving up after the very first attempt. Once the budget is exhausted the
guard is left pending/unresolved for the user to resolve manually via the
existing force-unlock path.
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


class TestFirstAttemptFailsAndRearmsForRetry:
    def test_timed_out_first_attempt_arms_a_second_timer(self, monkeypatch):
        revert_calls = []

        def _always_fails(ids):
            revert_calls.append(ids)
            return [
                (iid, False,
                 "Elevated operation timed out (still running in the background, outcome unknown)",
                 monitors.OUTCOME_AMBIGUOUS)
                for iid in ids
            ]

        api = _api_with_confirmed_disable(monkeypatch, _always_fails)
        guard = api._pending_guard
        assert len(_FakeTimer.instances) == 1
        assert api._pending_guard_attempt == 1

        first_timer = _FakeTimer.instances[0]
        future = time.time() + 3600
        monkeypatch.setattr("quickres.webview.bridge.time.time", lambda: future)
        first_timer.function()

        assert revert_calls == [["DISPLAY\\A\\1"]]
        assert guard.resolved is False, "a failed revert must not be reported as resolved"
        assert api._pending_guard is guard, "the same guard must stay armed for a retry"
        assert api._pending_guard_attempt == 2
        assert len(_FakeTimer.instances) == 2, "a retry timer must be armed after a failed attempt"
        retry_timer = _FakeTimer.instances[1]
        assert retry_timer.started is True
        from quickres.webview import bridge as bridge_mod
        assert retry_timer.interval == bridge_mod._AUTO_REVERT_RETRY_DELAY_S
        # On-disk crash-recovery record must survive an unresolved attempt.
        assert config.load_pending() is not None


class TestRetrySucceedsResolvesGuard:
    def test_second_attempt_succeeding_resolves_the_guard_and_clears_the_record(self, monkeypatch):
        attempts = {"n": 0}

        def _fails_then_succeeds(ids):
            attempts["n"] += 1
            ok = attempts["n"] >= 2
            kind = monitors.OUTCOME_CONFIRMED if ok else monitors.OUTCOME_GENUINE_FAILURE
            return [
                (iid, ok, "ok" if ok else "Elevation was cancelled or failed", kind)
                for iid in ids
            ]

        api = _api_with_confirmed_disable(monkeypatch, _fails_then_succeeds)
        guard = api._pending_guard

        monkeypatch.setattr(
            "quickres.webview.bridge.time.time", _AdvancingClock(time.time() + 3600)
        )

        # First attempt fails -> arms a retry.
        _FakeTimer.instances[0].function()
        assert guard.resolved is False
        assert len(_FakeTimer.instances) == 2

        # Retry attempt succeeds -> guard resolves, record clears, no third
        # timer is armed.
        _FakeTimer.instances[1].function()

        assert guard.resolved is True
        assert config.load_pending() is None
        assert len(_FakeTimer.instances) == 2, "a resolved guard must not arm a further retry"


class TestAllRetriesExhaustedStaysPendingForManualRecovery:
    def test_exhausting_the_attempt_budget_leaves_guard_pending_and_force_unlock_still_works(
        self, monkeypatch
    ):
        from quickres.webview import bridge as bridge_mod

        revert_calls = []

        def _always_fails(ids):
            revert_calls.append(ids)
            return [
                (iid, False, "Elevation was cancelled or failed", monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ]

        api = _api_with_confirmed_disable(monkeypatch, _always_fails)
        guard = api._pending_guard

        monkeypatch.setattr(
            "quickres.webview.bridge.time.time", _AdvancingClock(time.time() + 3600)
        )

        for expected_attempts_armed in range(1, bridge_mod._AUTO_REVERT_MAX_ATTEMPTS):
            timer = _FakeTimer.instances[-1]
            timer.function()

        # Exactly _AUTO_REVERT_MAX_ATTEMPTS timers were ever armed (initial +
        # retries up to the budget) -- no further retry after the last one.
        assert len(_FakeTimer.instances) == bridge_mod._AUTO_REVERT_MAX_ATTEMPTS
        assert len(revert_calls) == bridge_mod._AUTO_REVERT_MAX_ATTEMPTS - 1

        # Fire the final budgeted attempt -- it also fails, and the budget
        # is now exhausted: no further timer is armed.
        _FakeTimer.instances[-1].function()

        assert len(revert_calls) == bridge_mod._AUTO_REVERT_MAX_ATTEMPTS
        assert guard.resolved is False
        assert len(_FakeTimer.instances) == bridge_mod._AUTO_REVERT_MAX_ATTEMPTS, (
            "no further retry timer may be armed once the attempt budget is exhausted"
        )
        assert config.load_pending() is not None, (
            "the crash-recovery record must survive so the user can still "
            "recover manually"
        )

        # The existing manual force-unlock path must still work from here.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness",
            lambda helper_pid, owner_pid, **kwargs: __import__(
                "quickres.recovery", fromlist=["Liveness"]
            ).Liveness.DEAD,
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.read_op_result", lambda path, app_dir: None
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.sample_device_states",
            lambda ids: {},
        )
        result = api.force_unlock_pending()
        assert result["ok"] is True


class TestRetryNeverFiresAgainstASupersededGuard:
    def test_confirmed_guard_does_not_get_a_retry_armed_after_keep_disabled(self, monkeypatch):
        def _always_fails(ids):
            return [
                (iid, False, "Elevation was cancelled or failed", monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ]

        api = _api_with_confirmed_disable(monkeypatch, _always_fails)
        guard = api._pending_guard

        api.keep_disabled()  # confirms + cancels the live timer
        assert guard.resolved is True

        # A stray already-in-flight timer firing after confirmation must
        # remain the existing safe no-op -- and must never arm a retry.
        future = time.time() + 3600
        first_timer = _FakeTimer.instances[0]
        api._resolve_guard_unbounded_under_lock(guard, now=future)
        assert len(_FakeTimer.instances) == 1, "confirmed guard must never get a retry timer armed"

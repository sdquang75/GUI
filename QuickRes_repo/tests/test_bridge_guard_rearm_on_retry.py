"""Round 32 CONFIRMED finding (monitors.py's PendingDisableGuard, wired
through bridge.py's shared _arm_guard_timer primitive): is_expired()/check()
used to key off the guard's ORIGINAL armed_at/timeout_s only, set once at
construction. _arm_guard_timer -- reused by both the initial 10s grace
period (_arm_auto_revert_guard) and every bounded backoff retry
(_maybe_retry_auto_revert) -- only ever started a new real threading.Timer;
it never updated the guard's own schedule. So once a guard passed its FIRST
deadline, is_expired stayed permanently True for the rest of its life, no
matter how far in the future the real next scheduled retry actually was.

Any of the three call sites that can reach
_resolve_guard_unbounded_under_lock outside the timer that is actually
still pending -- webview/app.py's window-close handler and confirm_update
both call it with source_timer=None, which skips the stale-timer guard at
bridge.py:2093 entirely -- could therefore fire a second, out-of-schedule
live elevation attempt as soon as the guard had expired once, well ahead of
the real scheduled retry.

Fixed by _arm_guard_timer calling the new guard.rearm(now, delay_s) every
time it (re-)arms a real timer, keeping the guard's own is_expired() in
lockstep with whichever real timer is actually still pending.
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


class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_a_no_source_timer_resolve_ahead_of_the_real_retry_does_not_double_revert(monkeypatch):
    clock = _Clock(1000.0)
    monkeypatch.setattr("quickres.webview.bridge.time.time", clock)

    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True}],
    )

    revert_calls = []

    def _fake_set_monitors_enabled(ids, enabled, **kwargs):
        if enabled:
            revert_calls.append(list(ids))
            # Simulate a missed/declined UAC prompt on every revert attempt
            # -- the target never genuinely comes back, so the guard stays
            # unresolved and eligible for the bounded retry budget.
            return [(iid, False, "still stuck", monitors.OUTCOME_GENUINE_FAILURE) for iid in ids]
        return [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids]

    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.set_monitors_enabled",
        _fake_set_monitors_enabled,
    )

    api = Api()
    # t=0: confirmed disable arms the guard with the standard 10s window.
    result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
    assert result["ok"] is True
    guard = api._pending_guard
    assert guard is not None
    first_timer = _FakeTimer.instances[0]
    assert first_timer.interval == 10.0

    # t=10: the scheduled auto-revert timer fires. The revert attempt fails
    # for every target (missed UAC prompt), so the guard re-arms a bounded
    # backoff retry for t=15 (_AUTO_REVERT_RETRY_DELAY_S == 5.0).
    clock.t = 1010.0
    api._resolve_guard_unbounded_under_lock(guard, now=1010.0, source_timer=first_timer)
    assert revert_calls == [["DISPLAY\\A\\1"]], "exactly one revert attempt so far"
    assert guard.resolved is False
    assert len(_FakeTimer.instances) == 2, "a bounded retry timer must have been re-armed"
    second_timer = _FakeTimer.instances[1]
    assert second_timer.interval == 5.0

    # t=12: three seconds later -- well before the real t=15 retry -- some
    # other caller resolves the guard with no source_timer, exactly like
    # webview/app.py's window-close handler or confirm_update.
    triggered = api._resolve_guard_unbounded_under_lock(guard, now=1012.0)

    assert revert_calls == [["DISPLAY\\A\\1"]], (
        "a resolve call ahead of the real scheduled retry must NOT fire a "
        "second, out-of-schedule live revert attempt"
    )
    assert api._pending_guard_attempt == 2, (
        "the attempt budget must not have been consumed out of schedule"
    )

    # t=15: the real, scheduled retry timer finally fires -- this is when
    # the second revert attempt is actually due.
    api._resolve_guard_unbounded_under_lock(guard, now=1015.0, source_timer=second_timer)

    assert revert_calls == [["DISPLAY\\A\\1"], ["DISPLAY\\A\\1"]], (
        "the real scheduled retry must still fire normally"
    )

"""`revert_now()` used to confirm/tear down the live `PendingDisableGuard`
(`guard.confirm()`, `self._pending_guard = None`, cancelling its timer)
UNCONDITIONALLY before even attempting the actual re-enable call -- so a
manual "Revert now" click whose UAC prompt was missed or declined got zero
further automatic help. Contrast this with the automatic 10s-timer path
(`_resolve_guard_unbounded_under_lock` / `guard.check()` /
`_maybe_retry_auto_revert`), which only confirms the guard on a genuinely
successful revert and otherwise re-arms a bounded auto-retry timer (up to
`_AUTO_REVERT_MAX_ATTEMPTS`) for the exact same failure mode.

Fix: `revert_now()` now only confirms/tears down the guard AFTER
`monitors.set_monitors_enabled` reports genuine per-target success. A target
that did not succeed keeps the guard armed (trimmed to just the still-
failing targets) and is handed to the same `_maybe_retry_auto_revert`
mechanism the automatic path already uses, instead of forfeiting the
in-session retry budget just because the attempt was user-triggered.
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


def _api_with_confirmed_disable(monkeypatch, revert_fn):
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True}],
    )

    def _set_monitors_enabled(ids, enabled, **kwargs):
        if not enabled:
            return [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids]
        return revert_fn(ids)

    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.set_monitors_enabled", _set_monitors_enabled
    )
    api = Api()
    api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
    return api


class TestFailedManualRevertGetsTheSameRetryBudgetAsAnAutomaticOne:
    def test_failed_revert_now_leaves_the_guard_armed_and_arms_a_retry_timer(self, monkeypatch):
        revert_calls = []

        def _always_fails(ids):
            revert_calls.append(list(ids))
            return [
                (iid, False, "Elevation was cancelled or failed", monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ]

        api = _api_with_confirmed_disable(monkeypatch, _always_fails)
        guard = api._pending_guard
        assert len(_FakeTimer.instances) == 1, "the original 10s auto-revert timer"
        original_timer = _FakeTimer.instances[0]

        result = api.revert_now()

        assert result["ok"] is True
        assert revert_calls == [["DISPLAY\\A\\1"]]
        assert original_timer.cancelled is True, (
            "the original timer must still be cancelled up front so it "
            "never fires a redundant revert on top of this manual attempt"
        )
        assert api._pending_guard is guard, (
            "a failed manual revert must not destroy the guard -- it must "
            "stay available for the bounded auto-retry mechanism"
        )
        assert guard.resolved is False
        assert api._pending_guard_attempt == 2, (
            "a failed revert_now must consume the same attempt budget a "
            "failed automatic revert would"
        )
        assert len(_FakeTimer.instances) == 2, (
            "a retry timer must be armed after a failed manual revert, "
            "exactly like a failed automatic revert already gets"
        )
        retry_timer = _FakeTimer.instances[1]
        assert retry_timer.started is True
        from quickres.webview import bridge as bridge_mod
        assert retry_timer.interval == bridge_mod._AUTO_REVERT_RETRY_DELAY_S
        # The on-disk crash-recovery record must survive the failed attempt.
        record = config.load_pending()
        assert record is not None
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}

    def test_the_rearmed_retry_timer_firing_later_can_still_succeed(self, monkeypatch):
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

        result = api.revert_now()
        assert result["ok"] is True
        assert guard.resolved is False
        assert len(_FakeTimer.instances) == 2

        future = time.time() + 3600
        monkeypatch.setattr("quickres.webview.bridge.time.time", lambda: future)
        _FakeTimer.instances[1].function()

        assert guard.resolved is True
        assert config.load_pending() is None


class TestSuccessfulManualRevertStillConfirmsTheGuard:
    def test_successful_revert_now_confirms_and_clears_the_guard_as_before(self, monkeypatch):
        api = _api_with_confirmed_disable(
            monkeypatch,
            lambda ids: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        guard = api._pending_guard
        assert len(_FakeTimer.instances) == 1
        original_timer = _FakeTimer.instances[0]

        result = api.revert_now()

        assert result["ok"] is True
        assert original_timer.cancelled is True
        assert guard.resolved is True
        assert api._pending_guard is None
        assert api._pending_guard_timer is None
        assert config.load_pending() is None
        assert len(_FakeTimer.instances) == 1, (
            "a fully successful manual revert must not arm any retry timer"
        )

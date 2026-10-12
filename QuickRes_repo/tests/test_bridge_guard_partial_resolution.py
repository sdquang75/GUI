"""Round 5 corrective fixes (Stream B, bridge.py), two related HIGH findings
about multi-monitor batch granularity:

1. set_monitors_enabled's enable branch never touched self._pending_guard/
   self._pending_guard_timer/the on-disk pending_restore.json record --
   manually re-enabling a monitor still covered by ANOTHER disable
   operation's active 10s auto-revert guard left that guard armed against
   now-stale state (it could later fire a redundant, or worse conflicting,
   revert against a monitor the user already re-enabled themselves). Fixed
   (this round) by resolving/cancelling the whole guard the moment any of
   its targets gets manually enabled -- PendingDisableGuard only supports a
   single-shot resolve, so a partial-target resolution isn't attempted here;
   cancelling the entire guard is the simpler, safe option.

   NOTE (round 11 HIGH finding): whole-guard cancellation on ANY overlap
   turned out to have a real bug -- enabling ONE target out of a
   multi-target guard destroyed auto-revert protection AND the on-disk
   record for every OTHER, still-disabled target too. `PendingDisableGuard`
   gained real partial-target resolution (`remove_targets`) and
   `_resolve_guard_for_enabled_ids` now only fully cancels the guard when
   the enabled ids cover EVERY remaining target; see
   `tests/test_bridge_guard_partial_target_resolution.py` for that
   behavior's own coverage.
   `test_enable_branch_trims_record_leaving_timed_out_target` below is
   updated accordingly (a strict-subset enable now preserves the OTHER
   confirmed target's record entry too, not just the timed-out one).

2. config.clear_pending() used to be called unconditionally whenever a
   guard resolves (keep_disabled/revert_now/_resolve_guard_unbounded_under_lock),
   destroying the ENTIRE on-disk record even when the guard's own
   target_ids are a strict subset of the record's full multi-monitor
   'targets' list (e.g. a 3-monitor batch disable where 2 confirmed and got
   a guard while 1 timed out per round 4's fix and is still legitimately
   pending crash-recovery, all three recorded in the SAME record). Fixed by
   a shared `Api._clear_or_trim_pending_record(guard)` helper that only
   removes the guard's own target_ids from the record, re-saving the
   trimmed record when other targets remain and only deleting the file
   outright when none do.
"""
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


def _single_confirmed_disable(monkeypatch, instance_id="DISPLAY\\A\\1", friendly_name="A"):
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [{"instance_id": instance_id, "friendly_name": friendly_name, "enabled": True}],
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.set_monitors_enabled",
        lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
    )
    api = Api()
    api.set_monitors_enabled([instance_id], False)
    return api


class TestEnablingOverlappingGuardTargetResolvesGuard:
    """(a) enabling a monitor covered by an active pending guard resolves
    that guard rather than leaving it armed against stale state."""

    def test_enable_resolves_the_overlapping_guard(self, monkeypatch):
        api = _single_confirmed_disable(monkeypatch)
        guard = api._pending_guard
        assert guard is not None
        assert guard.resolved is False
        timer = _FakeTimer.instances[0]
        assert timer.cancelled is False

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        assert result["ok"] is True
        assert guard.resolved is True, "the guard must be resolved, not left armed"
        assert timer.cancelled is True, "the stale auto-revert timer must be cancelled"

    def test_enable_prevents_a_later_stale_auto_revert(self, monkeypatch):
        revert_calls = []
        api = _single_confirmed_disable(monkeypatch)
        guard = api._pending_guard

        def _fake_enable(ids, enabled, **kwargs):
            if enabled:
                revert_calls.append(ids)
            return [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fake_enable
        )
        api.set_monitors_enabled(["DISPLAY\\A\\1"], True)
        revert_calls.clear()  # discard the manual-enable call itself

        # Simulate the (now-stale) timer still firing later -- must be a
        # no-op since the guard was already resolved by the manual enable.
        timer = _FakeTimer.instances[0]
        timer.function()

        assert revert_calls == [], (
            "a guard resolved by a manual enable must never fire a later "
            "stale auto-revert against the monitor the user re-enabled"
        )

    def test_enable_of_unrelated_monitor_does_not_touch_an_active_guard(self, monkeypatch):
        api = _single_confirmed_disable(monkeypatch)
        guard = api._pending_guard

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        result = api.set_monitors_enabled(["DISPLAY\\B\\2"], True)

        assert result["ok"] is True
        assert guard.resolved is False, "an unrelated enable must not touch an active guard"
        assert api._pending_guard is guard


class TestPendingRecordTrimmedNotDestroyed:
    """(b) resolving a guard whose target_ids are a strict subset of a
    multi-target on-disk record trims the record, leaving the other
    still-pending target's crash-recovery info intact."""

    def _three_monitor_disable_two_confirmed_one_timeout(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
                {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B", "enabled": True},
                {"instance_id": "DISPLAY\\C\\3", "friendly_name": "C", "enabled": True},
            ],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                ("DISPLAY\\A\\1", True, "ok", monitors.OUTCOME_CONFIRMED),
                ("DISPLAY\\B\\2", True, "ok", monitors.OUTCOME_CONFIRMED),
                ("DISPLAY\\C\\3", False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS),
            ],
        )
        api = Api()
        api.set_monitors_enabled(
            ["DISPLAY\\A\\1", "DISPLAY\\B\\2", "DISPLAY\\C\\3"], False
        )
        return api

    def test_keep_disabled_trims_record_leaving_timed_out_target(self, monkeypatch):
        api = self._three_monitor_disable_two_confirmed_one_timeout(monkeypatch)
        record_before = config.load_pending()
        assert {t["instance_id"] for t in record_before["targets"]} == {
            "DISPLAY\\A\\1", "DISPLAY\\B\\2", "DISPLAY\\C\\3",
        }
        assert api._pending_guard.target_ids == ["DISPLAY\\A\\1", "DISPLAY\\B\\2"]

        result = api.keep_disabled()

        assert result["ok"] is True
        record_after = config.load_pending()
        assert record_after is not None, (
            "trimming must not destroy DISPLAY\\C\\3's still-unresolved "
            "crash-recovery info"
        )
        assert {t["instance_id"] for t in record_after["targets"]} == {"DISPLAY\\C\\3"}

    def test_revert_now_trims_record_leaving_timed_out_target(self, monkeypatch):
        api = self._three_monitor_disable_two_confirmed_one_timeout(monkeypatch)

        result = api.revert_now()

        assert result["ok"] is True
        record_after = config.load_pending()
        assert record_after is not None
        assert {t["instance_id"] for t in record_after["targets"]} == {"DISPLAY\\C\\3"}

    def test_auto_revert_under_lock_trims_record_leaving_timed_out_target(self, monkeypatch):
        import time

        api = self._three_monitor_disable_two_confirmed_one_timeout(monkeypatch)
        guard = api._pending_guard

        future = time.time() + 3600
        api._resolve_guard_unbounded_under_lock(guard, now=future)

        assert guard.resolved is True
        record_after = config.load_pending()
        assert record_after is not None
        assert {t["instance_id"] for t in record_after["targets"]} == {"DISPLAY\\C\\3"}

    def test_enable_branch_trims_only_the_enabled_target_leaving_the_rest(self, monkeypatch):
        # Round 11 HIGH fix: enabling only A (a strict subset of the guard's
        # target_ids == [A, B]) must trim ONLY A's own entry -- B is still
        # disabled and still covered by the (now partially-resolved) guard,
        # so its crash-recovery entry must survive alongside C's (timed out,
        # unrelated to any guard). Before the fix, this used to cancel the
        # WHOLE guard on any overlap and trim B's entry too, leaving only C.
        api = self._three_monitor_disable_two_confirmed_one_timeout(monkeypatch)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        assert result["ok"] is True
        record_after = config.load_pending()
        assert record_after is not None
        assert {t["instance_id"] for t in record_after["targets"]} == {
            "DISPLAY\\B\\2", "DISPLAY\\C\\3",
        }
        assert api._pending_guard is not None, "B's guard must still be armed"
        assert api._pending_guard.resolved is False
        assert api._pending_guard.target_ids == ["DISPLAY\\B\\2"]


class TestFullRecordClearingStillWorks:
    """(c) resolving a guard that covers ALL targets in the record still
    fully clears it -- no regression of the simple single-target case."""

    def test_keep_disabled_fully_clears_single_target_record(self, monkeypatch):
        api = _single_confirmed_disable(monkeypatch)
        assert config.load_pending() is not None

        result = api.keep_disabled()

        assert result["ok"] is True
        assert config.load_pending() is None

    def test_revert_now_fully_clears_single_target_record(self, monkeypatch):
        api = _single_confirmed_disable(monkeypatch)
        assert config.load_pending() is not None

        result = api.revert_now()

        assert result["ok"] is True
        assert config.load_pending() is None

    def test_auto_revert_fully_clears_single_target_record(self, monkeypatch):
        import time

        api = _single_confirmed_disable(monkeypatch)
        guard = api._pending_guard

        future = time.time() + 3600
        api._resolve_guard_unbounded_under_lock(guard, now=future)

        assert config.load_pending() is None

    def test_enable_branch_fully_clears_single_target_record(self, monkeypatch):
        api = _single_confirmed_disable(monkeypatch)
        assert config.load_pending() is not None

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        assert result["ok"] is True
        assert config.load_pending() is None

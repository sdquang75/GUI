"""Round 11 corrective fixes (Stream A, bridge.py + monitors.py):

1. HIGH -- `_resolve_guard_for_enabled_ids` (round 5) cancelled the WHOLE
   `PendingDisableGuard` the moment ANY of its target_ids overlapped the
   enabled instance_ids -- a deliberate round-5 tradeoff ("PendingDisableGuard
   only supports single-shot resolve"). Manually enabling monitor A out of a
   3-monitor batch disable (A, B, C) silently destroyed auto-revert
   protection AND the on-disk crash-recovery record for B and C too, even
   though they remained physically disabled. `PendingDisableGuard.remove_targets`
   now gives real partial-target resolution: when the enabled ids are a
   STRICT SUBSET of the guard's current targets, only those targets are
   dropped (guard/timer, and the matching on-disk record entries, stay
   intact for the rest); full cancellation only happens when the enabled
   ids cover every remaining target.

2. MEDIUM -- `force_unlock_pending()` marks the on-disk record's
   `unlocked_at` but has no live guard object to resolve (a force-unlocked
   target is, by construction, one whose disable never got a guard at all --
   see `_finalize_disable_outcome`). Manually enabling that monitor
   afterward only ever ran the guard-overlap check (a no-op, since there is
   no guard), leaving the stale "unlocked, unconfirmed" record entry on
   disk for the monitor for the rest of the session. The enable branch now
   also trims a target's own entry out of an unlocked on-disk record.
"""
import pytest

from quickres.webview.bridge import Api
from quickres import config, monitors, recovery


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


_ALL_MONITORS = [
    {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
    {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B", "enabled": True},
    {"instance_id": "DISPLAY\\C\\3", "friendly_name": "C", "enabled": True},
]


def _two_monitor_disable_both_confirmed(monkeypatch):
    """A + B disabled together, both confirmed -> a single guard covering
    both, one on-disk record with both targets."""
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors", lambda: _ALL_MONITORS,
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.set_monitors_enabled",
        lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
    )
    api = Api()
    api.set_monitors_enabled(["DISPLAY\\A\\1", "DISPLAY\\B\\2"], False)
    return api


class TestPartialEnablePreservesProtectionForOtherTargets:
    """(a) enabling ONE monitor out of a multi-target guard must not
    silently destroy auto-revert protection (or the crash-recovery record)
    for the OTHER, still-disabled targets in the same batch."""

    def test_guard_stays_armed_for_the_remaining_target(self, monkeypatch):
        api = _two_monitor_disable_both_confirmed(monkeypatch)
        guard = api._pending_guard
        assert guard is not None
        timer = _FakeTimer.instances[0]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        assert result["ok"] is True
        assert api._pending_guard is guard, "the guard for B must not be torn down"
        assert guard.resolved is False, "B is still disabled and unprotected -- not resolved"
        assert guard.target_ids == ["DISPLAY\\B\\2"]
        assert timer.cancelled is False, "the auto-revert timer protecting B must stay armed"

    def test_on_disk_record_keeps_the_other_target_entry(self, monkeypatch):
        api = _two_monitor_disable_both_confirmed(monkeypatch)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        record = config.load_pending()
        assert record is not None, "B's crash-recovery entry must survive"
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\B\\2"}

    def test_a_later_stale_timer_fire_only_reverts_the_remaining_target(self, monkeypatch):
        import time

        revert_calls = []
        api = _two_monitor_disable_both_confirmed(monkeypatch)
        guard = api._pending_guard

        def _fake_enable(ids, enabled, **kwargs):
            if enabled:
                revert_calls.append(list(ids))
            return [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fake_enable
        )
        api.set_monitors_enabled(["DISPLAY\\A\\1"], True)
        revert_calls.clear()  # discard the manual-enable call itself

        future = time.time() + 3600
        api._resolve_guard_unbounded_under_lock(guard, now=future)

        assert revert_calls == [["DISPLAY\\B\\2"]], (
            "a later expiry must only revert the still-armed target, "
            "never re-touch the monitor the user already enabled themselves"
        )
        assert guard.resolved is True


class TestFullEnableStillFullyResolves:
    """(b) enabling every remaining target of a guard in one call must
    still fully resolve/clear it, exactly like the pre-existing simple
    single-target case -- no regression from adding partial resolution."""

    def test_enabling_both_targets_at_once_resolves_the_guard(self, monkeypatch):
        api = _two_monitor_disable_both_confirmed(monkeypatch)
        guard = api._pending_guard
        timer = _FakeTimer.instances[0]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        result = api.set_monitors_enabled(["DISPLAY\\A\\1", "DISPLAY\\B\\2"], True)

        assert result["ok"] is True
        assert guard.resolved is True
        assert api._pending_guard is None
        assert timer.cancelled is True
        assert config.load_pending() is None

    def test_enabling_the_remaining_target_after_a_prior_partial_enable_resolves_it(
        self, monkeypatch
    ):
        api = _two_monitor_disable_both_confirmed(monkeypatch)
        guard = api._pending_guard

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        api.set_monitors_enabled(["DISPLAY\\A\\1"], True)
        assert guard.resolved is False

        api.set_monitors_enabled(["DISPLAY\\B\\2"], True)

        assert guard.resolved is True
        assert api._pending_guard is None
        assert config.load_pending() is None


class TestForceUnlockThenEnableClearsStaleRecord:
    """(c) manually enabling a monitor after force_unlock_pending() was
    used on it must clear its stale on-disk record entry, not leave the
    crash-recovery UI reporting an "unlocked, unconfirmed" state forever."""

    def _timed_out_disable_then_force_unlock(self, monkeypatch):
        # A timeout result never arms a guard at all (see
        # _finalize_disable_outcome), so this reaches force_unlock_pending
        # with self._pending_guard staying None -- the realistic path to a
        # force-unlockable record.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [_ALL_MONITORS[0]],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids
            ],
        )
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        assert api._pending_guard is None
        assert config.load_pending() is not None

        monkeypatch.setattr(
            "quickres.webview.bridge.recovery.force_unlockable", lambda outcomes: True
        )
        # Round 21 finding 3: unlocked_at is now stamped per-target, scoped
        # to the ids `outcome.can_force_unlock` actually flags -- an empty
        # outcomes list (the old stand-in used here) would no longer stamp
        # anything, so this now returns a realistic UNCONFIRMABLE outcome
        # for the target under test instead.
        monkeypatch.setattr(
            "quickres.webview.bridge.Api._resolve_pending_now",
            lambda self: [
                recovery.PendingOutcome(
                    resolution=recovery.Resolution.UNCONFIRMABLE,
                    instance_id="DISPLAY\\A\\1", friendly_name="A",
                    message="Could not confirm", elapsed_s=200.0, can_force_unlock=True,
                )
            ],
        )
        api.force_unlock_pending()
        record = config.load_pending()
        assert record is not None
        by_id = {t["instance_id"]: t for t in record["targets"]}
        assert by_id["DISPLAY\\A\\1"].get("unlocked_at") is not None
        return api

    def test_enabling_the_force_unlocked_monitor_clears_its_record_entry(self, monkeypatch):
        api = self._timed_out_disable_then_force_unlock(monkeypatch)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        assert result["ok"] is True
        assert config.load_pending() is None, (
            "the stale force-unlocked record entry must be cleared once the "
            "monitor is manually enabled, not linger for the rest of the session"
        )

"""Round 22 R3 Reliability finding: `Api.set_monitors_enabled`'s `enabled`
branch used to call `_resolve_guard_for_enabled_ids` and
`_clear_force_unlocked_targets_from_pending` UNCONDITIONALLY, BEFORE calling
`monitors.set_monitors_enabled(..., True)` -- tearing down the auto-revert
guard and trimming/deleting the on-disk `pending_restore.json` crash-recovery
entry before the elevated re-enable call's outcome was even known.

`monitors.set_monitors_enabled` has a real, documented failure path: a
declined/failed elevation (or any other genuine per-target failure) reports
`(instance_id, False, message, OUTCOME_GENUINE_FAILURE)` for every affected
target, and the device stays in its PRIOR (disabled) state. Tearing the
guard/record down first meant a user who declined the UAC prompt while
re-enabling a monitor ended up with it STILL disabled, but with no auto-revert
guard and no on-disk crash-recovery record left to protect it.

The fix reorders the branch to call `monitors.set_monitors_enabled` FIRST,
then only clear/trim the guard and on-disk record for targets whose enable
outcome was `ok=True` -- mirroring how `_finalize_disable_outcome` already
scopes its own guard-arming to `confirmed_ids` on the disable side. A target
whose enable genuinely fails keeps its pre-existing guard/on-disk protection
exactly as if the enable attempt had never happened.
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


_ALL_MONITORS = [
    {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
    {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B", "enabled": True},
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


class TestFailedEnableKeepsGuardAndRecordIntact:
    """A genuinely failed re-enable (declined UAC / OUTCOME_GENUINE_FAILURE)
    must leave that target's guard and on-disk crash-recovery entry exactly
    as they were -- the device is still disabled and still needs its
    existing protection."""

    def test_failed_enable_does_not_clear_the_guard(self, monkeypatch):
        api = _two_monitor_disable_both_confirmed(monkeypatch)
        guard = api._pending_guard
        assert guard is not None
        timer = _FakeTimer.instances[0]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, "Elevation was cancelled or failed", monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ],
        )
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        assert result["ok"] is True
        assert result["data"]["results"][0][1] is False, "the failure must still be reported to the caller"
        assert api._pending_guard is guard, "a failed enable must not tear down the guard"
        assert guard.resolved is False
        assert set(guard.target_ids) == {"DISPLAY\\A\\1", "DISPLAY\\B\\2"}, (
            "A's own guard coverage must survive its own failed re-enable attempt"
        )
        assert timer.cancelled is False, "the auto-revert timer must stay armed for the still-disabled A"

    def test_failed_enable_does_not_trim_the_on_disk_record(self, monkeypatch):
        api = _two_monitor_disable_both_confirmed(monkeypatch)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, "Elevation was cancelled or failed", monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ],
        )
        api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        record = config.load_pending()
        assert record is not None, "A's crash-recovery entry must survive its own failed re-enable"
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1", "DISPLAY\\B\\2"}


class TestSuccessfulEnableStillClearsAsBefore:
    """Regression coverage: a successful enable must still clear the guard
    and trim the on-disk record exactly like before the reordering fix."""

    def test_successful_enable_clears_the_guard_and_record(self, monkeypatch):
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

    def test_mixed_batch_only_clears_the_succeeded_target(self, monkeypatch):
        """One target succeeds, the other genuinely fails, in the same
        enable call -- only the succeeded target's protection is cleared."""
        api = _two_monitor_disable_both_confirmed(monkeypatch)
        guard = api._pending_guard

        def _mixed_enable(ids, enabled, **kwargs):
            out = []
            for iid in ids:
                if iid == "DISPLAY\\A\\1":
                    out.append((iid, True, "ok", monitors.OUTCOME_CONFIRMED))
                else:
                    out.append((iid, False, "Elevation was cancelled or failed", monitors.OUTCOME_GENUINE_FAILURE))
            return out

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _mixed_enable
        )
        result = api.set_monitors_enabled(["DISPLAY\\A\\1", "DISPLAY\\B\\2"], True)

        assert result["ok"] is True
        assert api._pending_guard is guard, "B's failed enable must keep the guard object alive"
        assert guard.resolved is False
        assert guard.target_ids == ["DISPLAY\\B\\2"], "A must be dropped, B must remain covered"

        record = config.load_pending()
        assert record is not None
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\B\\2"}

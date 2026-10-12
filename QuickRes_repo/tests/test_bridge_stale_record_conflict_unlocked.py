"""`_check_no_stale_record_conflict` used to check a target's liveness
without ever looking at whether that target had already been force-unlocked
(`force_unlock_pending` stamps a per-target `unlocked_at`, but by design
never removes the target's on-disk entry). After a crash mid-disable and an
app relaunch, a target's on-disk `owner_pid` belongs to the exited process,
so `monitors.process_liveness`'s PID-reuse guard can only ever answer
`UNKNOWN` for it -- `DEAD` is reachable only when `owner_pid` matches the
CURRENT process. If the user force-unlocks that target (via the crash-
recovery UI) and then tries to disable the exact same monitor again, the old
code raised "wait for it to resolve" forever, since that liveness value can
never become DEAD for a record surviving a crash/relaunch -- the disable was
refused indefinitely unless the user happened to click "Enable" on it first.

Fix: a target whose on-disk entry already carries a truthy `unlocked_at`
(this session's own force-unlock stamp, or a legacy record-level stamp) is
treated as already resolved by the user's own action -- the liveness check
is skipped and the fresh disable is allowed to proceed. A target that has
NOT been force-unlocked keeps the original protection unchanged.
"""
import time

import pytest

from quickres.webview.bridge import Api
from quickres import config, monitors, recovery


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


def _known_monitor(monkeypatch, instance_id="DISPLAY\\A\\1", friendly_name="A"):
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [{"instance_id": instance_id, "friendly_name": friendly_name, "enabled": True}],
    )


def _write_stale_record(instance_id="DISPLAY\\A\\1", friendly_name="A", unlocked_at=None):
    """Simulate the on-disk state left behind after a crash + relaunch +
    force-unlock: `owner_pid` belongs to a process that is no longer this
    one, so `monitors.process_liveness` can never resolve it to DEAD.
    """
    config.save_pending({
        "action": "disable",
        "targets": [{
            "instance_id": instance_id,
            "friendly_name": friendly_name,
            "helper_pid": 1111,
            "helper_pid_start_time": None,
            "owner_pid": 999999,
            "unlocked_at": unlocked_at,
        }],
        "result_file": None,
        "helper_pid": 1111,
        "helper_pid_start_time": None,
        "owner_pid": 999999,
        "started_at": time.time() - 300.0,
        "unlocked_at": None,
    })


class TestAlreadyForceUnlockedTargetIsEligibleForFreshDisable:
    @pytest.mark.parametrize("liveness", [recovery.Liveness.ALIVE, recovery.Liveness.UNKNOWN])
    def test_disable_proceeds_without_raising_when_target_was_force_unlocked(
        self, monkeypatch, liveness
    ):
        _known_monitor(monkeypatch)
        _write_stale_record(unlocked_at=time.time() - 60)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness", lambda *a, **k: liveness
        )
        launched = {"called": False}

        def _fresh_helper(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
            launched["called"] = True
            return [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fresh_helper
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True, (
            "a target already force-unlocked must be eligible for a fresh "
            "disable, not blocked forever on a liveness value that can "
            "never resolve to DEAD for a stale cross-process record"
        )
        assert launched["called"] is True

    def test_legacy_record_level_unlocked_at_also_makes_the_target_eligible(self, monkeypatch):
        # Backward compatibility: a record written before per-target
        # unlocked_at existed stamped the field record-wide instead.
        _known_monitor(monkeypatch)
        _write_stale_record(unlocked_at=None)
        record = config.load_pending()
        record["unlocked_at"] = time.time() - 60
        config.save_pending(record)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness",
            lambda *a, **k: recovery.Liveness.UNKNOWN,
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True


class TestNotYetForceUnlockedTargetStillBlocksExactlyAsBefore:
    @pytest.mark.parametrize("liveness", [recovery.Liveness.ALIVE, recovery.Liveness.UNKNOWN])
    def test_retry_of_same_target_still_refused_when_never_force_unlocked(
        self, monkeypatch, liveness
    ):
        _known_monitor(monkeypatch)
        _write_stale_record(unlocked_at=None)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness", lambda *a, **k: liveness
        )
        launched = {"called": False}

        def _fail_if_launched(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
            launched["called"] = True
            return [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fail_if_launched
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is False
        assert result["kind"] == "error"
        assert launched["called"] is False, (
            "a target that was never force-unlocked must still be refused "
            "a retry while its liveness is not confirmed dead -- this is "
            "the existing, still-valid protection"
        )

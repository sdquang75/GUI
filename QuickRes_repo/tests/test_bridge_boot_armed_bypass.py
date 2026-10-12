"""1a: boot-armed deadlock fix.

recover_on_boot() re-arms self._op_lock (with self._boot_armed=True) on an
IN_FLIGHT crash-recovery outcome. Before this fix, every @bridge_op(lock=True)
method -- including the intended escape hatches -- failed the busy-check
forever afterwards, since nothing ever released the lock. These tests prove:

  (i)   recheck_pending (read-only) runs while boot-armed, WITHOUT releasing
        the lock or clearing the flag.
  (ii)  keep_disabled / force_unlock_pending (resolving ops) run while
        boot-armed AND release the lock + clear the flag on success, after
        which a normal set_monitors_enabled call succeeds instead of busy.
  (iii) set_monitors_enabled is NOT given the bypass -- it must still return
        kind=busy while boot-armed.
"""
import pytest

from quickres.webview.bridge import Api
from quickres import config
from quickres import recovery


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


def _boot_armed_api():
    api = Api()
    api._op_lock.acquire(blocking=False)
    api._lock_reason = "A monitor operation from a previous session is still resolving"
    api._boot_armed = True
    return api


class TestRecheckPendingBootArmedBypass:
    def test_succeeds_without_releasing_lock_or_clearing_flag(self, monkeypatch):
        monkeypatch.setattr(Api, "_resolve_pending_now", lambda self: [])
        api = _boot_armed_api()

        result = api.recheck_pending()

        assert result["ok"] is True
        assert api._op_lock.locked() is True
        assert api._boot_armed is True


class TestKeepDisabledBootArmedBypass:
    def test_succeeds_and_releases_lock_and_clears_flag(self):
        api = _boot_armed_api()

        result = api.keep_disabled()

        assert result["ok"] is True
        assert api._op_lock.locked() is False
        assert api._boot_armed is False

    def test_subsequent_set_monitors_enabled_no_longer_busy(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled: [(iid, True, "ok") for iid in ids],
        )
        api = _boot_armed_api()
        api.keep_disabled()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        assert result["ok"] is True
        assert result["kind"] == "ok"


class TestForceUnlockPendingBootArmedBypass:
    def test_succeeds_and_releases_lock_and_clears_flag(self, monkeypatch):
        unconfirmable_outcome = recovery.PendingOutcome(
            resolution=recovery.Resolution.UNCONFIRMABLE,
            instance_id="DISPLAY\\A\\1", friendly_name="A",
            message="Could not confirm", elapsed_s=200.0, can_force_unlock=True,
        )
        monkeypatch.setattr(Api, "_resolve_pending_now", lambda self: [unconfirmable_outcome])
        monkeypatch.setattr(
            "quickres.webview.bridge.config.load_pending",
            lambda: {"action": "disable", "targets": [{"instance_id": "DISPLAY\\A\\1"}]},
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.config.save_pending", lambda record: True
        )
        api = _boot_armed_api()

        result = api.force_unlock_pending()

        assert result["ok"] is True
        assert api._op_lock.locked() is False
        assert api._boot_armed is False


class TestSetMonitorsEnabledNeverBypassesBootArmed:
    def test_still_returns_busy_while_boot_armed(self):
        api = _boot_armed_api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        assert result["ok"] is False
        assert result["kind"] == "busy"

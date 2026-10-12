"""force_unlock_pending() used to compute outcomes = self._resolve_pending_now()
ONCE at the top, use it to decide which target ids are force-unlockable, stamp
unlocked_at on disk for those targets, then RETURN THAT SAME PRE-STAMP outcomes
list -- never recomputing the resolution after the stamp. panel.html's click
handler feeds this return value directly into resetPendingState(), so the
notice banner kept showing the stale pre-unlock message (e.g. "Could not
confirm -- helper is gone") instead of the correct post-unlock "Unlocked,
outcome unconfirmed" text, until the user reopened the Monitors modal or
restarted the app.

Fix: after stamping unlocked_at on disk, self._resolve_pending_now() is
recomputed before building the return value, so force_unlock_pending()'s OWN
return value reflects the just-created UNLOCKED_UNCONFIRMED state.
"""
import time

import pytest

from quickres.webview.bridge import Api
from quickres import config, recovery


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


def _write_unconfirmable_record():
    """A single target with no result file and a dead helper, past the
    expiry window -- resolves to UNCONFIRMABLE (can_force_unlock=True)."""
    config.save_pending({
        "action": "disable",
        "targets": [
            {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B"},
        ],
        "result_file": None,
        "helper_pid": None,
        "owner_pid": 999999,
        "started_at": time.time() - 300.0,
        "unlocked_at": None,
    })


class TestForceUnlockPendingReturnsPostUnlockOutcomes:
    def test_own_return_value_reflects_post_unlock_state(self, monkeypatch):
        _write_unconfirmable_record()
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.sample_device_states",
            lambda ids: {},
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness",
            lambda *a, **k: recovery.Liveness.DEAD,
        )
        api = Api()

        result = api.force_unlock_pending()
        assert result["ok"] is True

        outcomes = result["data"]["outcomes"]
        by_id = {o["instance_id"]: o for o in outcomes}
        # This is the crux of the finding: force_unlock_pending()'s OWN
        # returned outcomes -- not a separately re-queried resolution --
        # must already reflect the just-created UNLOCKED_UNCONFIRMED state,
        # not the stale pre-stamp UNCONFIRMABLE resolution.
        assert by_id["DISPLAY\\B\\2"]["resolution"] == recovery.Resolution.UNLOCKED_UNCONFIRMED.value

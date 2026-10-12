"""Round 21 finding 2 (HIGH): `keep_disabled()`'s boot/crash-recovered
branch (`self._pending_guard is None`) used to call `config.clear_pending()`
unconditionally, wiping the ENTIRE on-disk crash-recovery record instead of
only the target(s) this "keep disabled" click actually resolved -- losing
crash-recovery tracking for any OTHER, still-unresolved target that got
unioned into the same on-disk record (e.g. a "Disable all" batch where
target A confirmed and target B stayed UNCONFIRMABLE, both recorded
together; clicking "Keep disabled" wiped B's entry too, even though B's
on-disk entry was its ONLY safety net).

Fix: mirror `revert_now`'s existing per-target-scoped trimming -- only
targets whose resolution is settled (i.e. not `UNCONFIRMABLE`, which still
needs the record as its safety net) are trimmed from the on-disk record;
any other target's entry survives untouched.
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


def _write_mixed_batch_record():
    """Target A is confirmed disabled (device_state observed disabled),
    target B is UNCONFIRMABLE (helper is gone, still within expiry so not
    the IN_FLIGHT case) -- both in the SAME on-disk record, with no live
    `PendingDisableGuard` object backing either (the boot/crash-recovered
    path this finding is about)."""
    config.save_pending({
        "action": "disable",
        "targets": [
            {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A"},
            {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B"},
        ],
        "result_file": None,
        "helper_pid": None,
        "owner_pid": 999999,
        "started_at": time.time() - 300.0,
        "unlocked_at": None,
    })


class TestKeepDisabledBootRecoveredScoping:
    def test_keep_disabled_does_not_destroy_a_sibling_unconfirmable_target(self, monkeypatch):
        _write_mixed_batch_record()

        def fake_device_states(ids):
            # A: observed disabled (device_state False) -> DISABLED_CONFIRMED.
            # B: unknown -> falls through to UNCONFIRMABLE once liveness is
            # DEAD and it's past the expiry window (started_at is 300s ago).
            return {"DISPLAY\\A\\1": False}

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.sample_device_states", fake_device_states
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness",
            lambda *a, **k: recovery.Liveness.DEAD,
        )
        api = Api()
        assert api._pending_guard is None, "the boot/crash-recovered path never has a live guard"

        result = api.keep_disabled()

        assert result["ok"] is True
        record = config.load_pending()
        assert record is not None, (
            "target B is still UNCONFIRMABLE -- its on-disk safety net must survive"
        )
        remaining_ids = {t["instance_id"] for t in record["targets"]}
        assert remaining_ids == {"DISPLAY\\B\\2"}, (
            "keep_disabled must trim only the settled target (A) and leave "
            "the still-UNCONFIRMABLE sibling (B) untouched"
        )

    def test_keep_disabled_clears_the_record_when_every_target_is_settled(self, monkeypatch):
        config.save_pending({
            "action": "disable",
            "targets": [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A"}],
            "result_file": None,
            "helper_pid": None,
            "owner_pid": 999999,
            "started_at": time.time() - 300.0,
            "unlocked_at": None,
        })
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.sample_device_states",
            lambda ids: {"DISPLAY\\A\\1": False},
        )
        api = Api()

        result = api.keep_disabled()

        assert result["ok"] is True
        assert config.load_pending() is None

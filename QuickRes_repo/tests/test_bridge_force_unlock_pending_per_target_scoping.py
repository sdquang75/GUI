"""Round 21 finding 3 (medium): `force_unlock_pending()` used to stamp
`unlocked_at` on the WHOLE on-disk record (`record["unlocked_at"] =
time.time(); config.save_pending(record)`), not scoped to only the
target(s) that are actually UNCONFIRMABLE. In a mixed-batch scenario (one
already-resolved/confirmed target A alongside a still-UNCONFIRMABLE target
B in the SAME on-disk record), clicking the single global "Force unlock"
button (meant to free B) permanently mislabels A as
"Unlocked, outcome unconfirmed" forever too, since
`recovery.resolve_pending()`'s `unlocked_at is not None` check used to apply
record-wide, before any per-target check.

Fix: `unlocked_at` is now stamped per-target (only on the target dicts that
are actually force-unlockable), and `recovery.resolve_pending()` checks the
per-target field (falling back to the legacy record-level field for
backward compatibility with an already-on-disk record written before this
fix).
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
    target B is UNCONFIRMABLE (helper is gone, past the expiry window) --
    both in the SAME on-disk record, no live guard for either."""
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


class TestForceUnlockPendingPerTargetScoping:
    def test_force_unlocking_the_ambiguous_target_does_not_mislabel_the_confirmed_sibling(
        self, monkeypatch
    ):
        _write_mixed_batch_record()
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.sample_device_states",
            lambda ids: {"DISPLAY\\A\\1": False},
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness",
            lambda *a, **k: recovery.Liveness.DEAD,
        )
        api = Api()

        result = api.force_unlock_pending()
        assert result["ok"] is True

        record = config.load_pending()
        assert record is not None
        by_id = {t["instance_id"]: t for t in record["targets"]}
        assert by_id["DISPLAY\\B\\2"].get("unlocked_at") is not None, (
            "the genuinely UNCONFIRMABLE target must be stamped unlocked"
        )
        assert by_id["DISPLAY\\A\\1"].get("unlocked_at") is None, (
            "an already-confirmed sibling target must NOT be stamped "
            "unlocked just because a different target in the same record "
            "was force-unlocked"
        )

        # A subsequent resolve must not mislabel A as unconfirmed.
        api._op_lock.acquire()
        try:
            fresh_outcomes = api._resolve_pending_now()
        finally:
            api._op_lock.release()
        by_outcome_id = {o.instance_id: o for o in fresh_outcomes}
        assert by_outcome_id["DISPLAY\\A\\1"].resolution == recovery.Resolution.DISABLED_CONFIRMED
        assert by_outcome_id["DISPLAY\\B\\2"].resolution == recovery.Resolution.UNLOCKED_UNCONFIRMED


class TestResolvePendingBackwardCompatibleWithLegacyRecordLevelUnlockedAt:
    def test_legacy_record_level_unlocked_at_still_applies_to_every_target(self):
        # An on-disk record written before this fix (or by some other
        # legacy path) that only ever set the record-level field must keep
        # unlocking every target in it, matching the old behavior exactly.
        record = {
            "action": "disable",
            "targets": [
                {"instance_id": "id-0", "friendly_name": "A"},
                {"instance_id": "id-1", "friendly_name": "B"},
            ],
            "owner_pid": 111,
            "started_at": 1000.0,
            "unlocked_at": 2000.0,
        }
        outcomes = recovery.resolve_pending(
            record, now=2100.0, liveness=recovery.Liveness.DEAD,
            helper_results={}, device_states={},
        )
        assert len(outcomes) == 2
        for outcome in outcomes:
            assert outcome.resolution == recovery.Resolution.UNLOCKED_UNCONFIRMED

    def test_per_target_unlocked_at_only_applies_to_that_target(self):
        record = {
            "action": "disable",
            "targets": [
                {"instance_id": "id-0", "friendly_name": "A"},
                {"instance_id": "id-1", "friendly_name": "B", "unlocked_at": 2000.0},
            ],
            "owner_pid": 111,
            "started_at": 1000.0,
            "unlocked_at": None,
        }
        outcomes = recovery.resolve_pending(
            record, now=2100.0, liveness=recovery.Liveness.DEAD,
            helper_results={}, device_states={},
        )
        by_id = {o.instance_id: o for o in outcomes}
        assert by_id["id-1"].resolution == recovery.Resolution.UNLOCKED_UNCONFIRMED
        assert by_id["id-0"].resolution != recovery.Resolution.UNLOCKED_UNCONFIRMED

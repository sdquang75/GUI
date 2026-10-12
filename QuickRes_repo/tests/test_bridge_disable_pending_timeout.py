"""Round 4 finding 1 (bridge.py side): a False `ok` in monitors.py's
set_monitors_enabled() result tuples can mean either a genuine failure OR an
unresolved TIMEOUT (monitors.TIMEOUT_MESSAGE) -- the elevated helper may
still complete the disable moments later in the background. Api's disable
path used to clear the on-disk crash-recovery record (config.clear_pending())
whenever confirmed_ids was empty, treating a timeout exactly like a genuine
failure and destroying the record recover_on_boot needs to surface the
unresolved state on next launch. The fix: only clear the record when every
result is a genuine, confirmed failure -- never when any result is a
timeout/unknown outcome.
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


def _known_monitor(monkeypatch, instance_id="DISPLAY\\A\\1", friendly_name="A"):
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [{"instance_id": instance_id, "friendly_name": friendly_name, "enabled": True}],
    )


class TestPendingRecordSurvivesTimeoutOutcome:
    def test_timeout_outcome_does_not_clear_pending_record(self, monkeypatch):
        _known_monitor(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids
            ],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        assert config.load_pending() is not None, (
            "a timeout outcome is unknown, not a confirmed failure -- the "
            "crash-recovery record must survive for recover_on_boot"
        )

    def test_genuine_failure_still_clears_pending_record(self, monkeypatch):
        _known_monitor(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, "Elevation was cancelled or failed", monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        assert config.load_pending() is None, (
            "a genuine, confirmed failure has nothing left to protect -- the "
            "stale crash-recovery record must still be cleared"
        )

    def test_mixed_timeout_and_genuine_failure_does_not_clear(self, monkeypatch):
        # Even one unresolved-outcome result in the batch means the record
        # must survive -- some elevated targets could still resolve later.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
                {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B", "enabled": True},
            ],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                ("DISPLAY\\A\\1", False, "Elevated helper did not report a result",
                 monitors.OUTCOME_GENUINE_FAILURE),
                ("DISPLAY\\B\\2", False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS),
            ],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1", "DISPLAY\\B\\2"], False)

        assert result["ok"] is True
        assert config.load_pending() is not None

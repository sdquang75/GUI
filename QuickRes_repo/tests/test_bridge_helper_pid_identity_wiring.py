"""Round 18 finding (R4 Resilience): monitors.process_liveness() gained an
optional helper_pid_start_time PID-reuse identity guard, but nothing in
bridge.py captured or threaded it through -- the guard existed but was never
wired into production. This proves bridge.py now:

1. Captures the helper's process start time via monitors.get_process_start_time
   at the same moment it captures helper_pid (_save_helper_pid), and persists
   it on the pending record.
2. Passes that stored start time through to every monitors.process_liveness(
   helper_pid, owner_pid, ...) call site so a reused PID can be detected.
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


def _known_monitor(monkeypatch, instance_id="DISPLAY\\A\\1", friendly_name="A"):
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [{"instance_id": instance_id, "friendly_name": friendly_name, "enabled": True}],
    )


class TestSaveHelperPidCapturesStartTime:
    def test_save_helper_pid_persists_start_time_from_get_process_start_time(self, monkeypatch):
        _known_monitor(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.get_process_start_time",
            lambda pid: 123456789,
        )

        def _timeout_with_pid(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
            if on_helper_launched:
                on_helper_launched(4242)
            return [(iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _timeout_with_pid
        )

        api = Api()
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        assert result["ok"] is True

        record = config.load_pending()
        assert record["helper_pid"] == 4242
        assert record["helper_pid_start_time"] == 123456789

    def test_save_helper_pid_tolerates_unresolvable_start_time(self, monkeypatch):
        _known_monitor(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.get_process_start_time",
            lambda pid: None,
        )

        def _timeout_with_pid(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
            if on_helper_launched:
                on_helper_launched(4242)
            return [(iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _timeout_with_pid
        )

        api = Api()
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        assert result["ok"] is True

        record = config.load_pending()
        assert record["helper_pid_start_time"] is None


class TestProcessLivenessCallSitesReceiveStoredStartTime:
    def test_check_no_stale_record_conflict_passes_stored_start_time(self, monkeypatch):
        _known_monitor(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.get_process_start_time",
            lambda pid: 999,
        )

        def _timeout_with_pid(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
            if on_helper_launched:
                on_helper_launched(1111)
            return [(iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _timeout_with_pid
        )

        api = Api()
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        assert result["ok"] is True

        captured = {}

        def _capture_liveness(helper_pid, owner_pid, helper_pid_start_time=None):
            captured["helper_pid"] = helper_pid
            captured["helper_pid_start_time"] = helper_pid_start_time
            return recovery.Liveness.ALIVE

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness", _capture_liveness
        )

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        assert result["ok"] is False
        assert captured["helper_pid"] == 1111
        assert captured["helper_pid_start_time"] == 999

    def test_resolve_pending_now_passes_stored_start_time_to_process_liveness(self, monkeypatch):
        _known_monitor(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.get_process_start_time",
            lambda pid: 555,
        )

        def _timeout_with_pid(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
            if on_helper_launched:
                on_helper_launched(2222)
            return [(iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _timeout_with_pid
        )

        api = Api()
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        assert result["ok"] is True

        captured = {}

        def _capture_liveness(helper_pid, owner_pid, helper_pid_start_time=None):
            captured["helper_pid"] = helper_pid
            captured["helper_pid_start_time"] = helper_pid_start_time
            return recovery.Liveness.DEAD

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness", _capture_liveness
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.read_op_result", lambda *a, **k: {}
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.sample_device_states", lambda *a, **k: {}
        )

        outcomes = api.recheck_pending()
        assert outcomes["ok"] is True
        assert captured["helper_pid"] == 2222
        assert captured["helper_pid_start_time"] == 555

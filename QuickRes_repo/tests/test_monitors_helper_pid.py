"""1b: the elevated helper's real PID must be captured and threaded back,
instead of being permanently None (which fed 1a's deadlock trigger by
forcing monitors.process_liveness() to always resolve UNKNOWN).
"""
import quickres.monitors as monitors_mod
from quickres.monitors import set_monitors_enabled
from quickres.webview.bridge import Api
from quickres import config


def _stub_success_run(monkeypatch, results_by_id):
    monkeypatch.setattr(monitors_mod, "_wait_for_helper", lambda handle, timeout_s: True)
    monkeypatch.setattr(
        monitors_mod,
        "read_op_result",
        lambda path, app_dir: {
            "results": [
                {"instance_id": iid, "ok": ok, "message": message}
                for iid, (ok, message) in results_by_id.items()
            ]
        },
    )
    monkeypatch.setattr(
        monitors_mod, "sample_device_states", lambda ids: {iid: None for iid in ids}
    )


class TestOnHelperLaunchedCallback:
    def test_callback_fires_with_real_pid_before_wait_completes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "fake-handle")
        monkeypatch.setattr(
            monitors_mod.kernel32, "GetProcessId", lambda handle: 4321
        )
        _stub_success_run(monkeypatch, {"A": (True, "Disabled")})

        seen = []
        result = set_monitors_enabled(
            ["A"], False, app_dir=str(tmp_path), on_helper_launched=lambda pid: seen.append(pid)
        )

        assert seen == [4321]
        assert result == [("A", True, "Disabled", monitors_mod.OUTCOME_CONFIRMED)]

    def test_no_callback_never_touches_get_process_id(self, monkeypatch, tmp_path):
        # Existing callers that don't pass on_helper_launched must be
        # completely unaffected -- GetProcessId must not even be called
        # (the real handle in production is opaque; calling it unnecessarily
        # on e.g. a test double would blow up).
        monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "fake-handle")

        def _boom(handle):
            raise AssertionError("must not call GetProcessId without a callback")

        monkeypatch.setattr(monitors_mod.kernel32, "GetProcessId", _boom)
        _stub_success_run(monkeypatch, {"A": (True, "Disabled")})

        result = set_monitors_enabled(["A"], False, app_dir=str(tmp_path))

        assert result == [("A", True, "Disabled", monitors_mod.OUTCOME_CONFIRMED)]


class TestBridgePersistsRealHelperPid:
    def test_set_monitors_enabled_persists_non_none_helper_pid(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
        monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
        monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True}],
        )

        saved_records = []

        def _fake_set_monitors_enabled(instance_ids, enabled, *, result_path=None, on_helper_launched=None):
            if on_helper_launched is not None:
                on_helper_launched(7777)
            return [(iid, True, "Disabled", monitors_mod.OUTCOME_CONFIRMED) for iid in instance_ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            _fake_set_monitors_enabled,
        )

        api = Api()
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        pending = config.load_pending()
        assert pending is not None
        assert pending["helper_pid"] == 7777

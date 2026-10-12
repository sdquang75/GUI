"""Round 10 finding 1: `Api.set_monitors_enabled` had no guard against an
empty `instance_ids` list on the disable (enabled=False) branch. The
client's "Disable all" button can legitimately produce an empty array (e.g.
nothing is currently enabled), and the pre-fix code still proceeded through
the full crash-recovery record write and elevated-helper launch for zero
targets, needlessly prompting the user with a UAC dialog for nothing.

Fix: `set_monitors_enabled` now short-circuits to a no-op success response
`{"results": []}` before any elevation attempt whenever `instance_ids` is
empty, on both the enable and disable branches.
"""
import pytest

from quickres.webview.bridge import Api
from quickres import config


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


class TestEmptyInstanceIdsDisableIsNoop:
    def test_returns_empty_results_without_reaching_elevation(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError(
                "monitors.set_monitors_enabled (the elevation seam) must "
                "never be reached for an empty instance_ids disable call"
            )

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _boom
        )
        api = Api()

        result = api.set_monitors_enabled([], False)

        assert result["ok"] is True
        assert result["data"] == {"results": []}

    def test_does_not_write_a_pending_record(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("elevation seam must not be reached")
            ),
        )
        api = Api()

        api.set_monitors_enabled([], False)

        assert config.load_pending() is None

    def test_empty_instance_ids_enable_is_also_a_noop(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("elevation seam must not be reached")
            ),
        )
        api = Api()

        result = api.set_monitors_enabled([], True)

        assert result["ok"] is True
        assert result["data"] == {"results": []}

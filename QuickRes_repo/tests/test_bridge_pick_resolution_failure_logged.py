"""Round 10 finding 3: `pick_resolution`'s failure path was silent -- unlike
the identical underlying Win32 call reached via the hotkey path
(`hotkey.py`'s `_toggle`, which DOES log a failure via `log_msg`), a failed
`display.set_resolution` call from `pick_resolution` left zero trace in
quickres.log. This is the most common user-facing failure mode (clicking a
resolution preset and it failing).

Fix: `pick_resolution` now logs via `log_msg` on the failure path, matching
`hotkey.py`'s existing logging pattern/message style for the same
underlying `display.set_resolution` failure.
"""
import pytest

from quickres.webview.bridge import Api


@pytest.fixture(autouse=True)
def _stub_supported_resolutions(monkeypatch):
    monkeypatch.setattr(
        "quickres.webview.bridge.display.get_supported_resolutions",
        lambda: {(1920, 1080)},
    )
    yield


class TestPickResolutionLogsFailure:
    def test_failed_set_resolution_is_logged(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.display.set_resolution",
            lambda w, h: (False, "Elevation was cancelled or failed"),
        )
        logged = []
        monkeypatch.setattr("quickres.webview.bridge.log_msg", lambda msg: logged.append(msg))
        api = Api()

        result = api.pick_resolution(1920, 1080)

        assert result["ok"] is True
        assert result["data"]["ok"] is False
        assert len(logged) == 1
        assert "1920" in logged[0] and "1080" in logged[0]

    def test_successful_set_resolution_is_not_logged(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.display.set_resolution",
            lambda w, h: (True, "ok"),
        )
        logged = []
        monkeypatch.setattr("quickres.webview.bridge.log_msg", lambda msg: logged.append(msg))
        api = Api()

        result = api.pick_resolution(1920, 1080)

        assert result["ok"] is True
        assert result["data"]["ok"] is True
        assert logged == []

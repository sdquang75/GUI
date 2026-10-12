import pytest

from quickres import updater
from quickres.webview.bridge import Api


def test_check_updates_noop_when_not_frozen(monkeypatch):
    # APP-2: update checking only runs on a frozen (built exe) app.
    monkeypatch.setattr("quickres.webview.bridge.sys.frozen", False, raising=False)
    fetch_calls = []
    monkeypatch.setattr(
        "quickres.webview.bridge.updater.fetch_version_info",
        lambda: fetch_calls.append(1),
    )
    api = Api()

    result = api.check_updates()

    assert result["ok"] is True
    assert result["data"] is None
    assert fetch_calls == []


def test_confirm_update_system_exit_forces_os_exit(monkeypatch):
    monkeypatch.setattr(
        updater,
        "apply_update",
        lambda url, version_info=None: (_ for _ in ()).throw(SystemExit(0)),
    )
    exit_calls = []
    monkeypatch.setattr(updater.os, "_exit", lambda code: exit_calls.append(code))

    updater.confirm_update("https://example.com/QuickRes.exe")

    assert exit_calls == [0]


def test_confirm_update_other_exception_does_not_force_exit(monkeypatch):
    def _raise(url, version_info=None):
        raise ConnectionError("network is down")

    monkeypatch.setattr(updater, "apply_update", _raise)
    exit_calls = []
    monkeypatch.setattr(updater.os, "_exit", lambda code: exit_calls.append(code))

    with pytest.raises(ConnectionError):
        updater.confirm_update("https://example.com/QuickRes.exe")

    assert exit_calls == []


def test_bridge_confirm_update_reports_other_exception_without_force_exit(monkeypatch):
    # The bridge layer: a non-SystemExit failure must surface as ok:false
    # in the envelope, with the app staying alive (no os._exit call at all).
    def _raise(url, version_info=None):
        raise ConnectionError("network is down")

    monkeypatch.setattr("quickres.webview.bridge.updater.confirm_update", _raise)
    exit_calls = []
    monkeypatch.setattr("quickres.webview.bridge.updater.os._exit", lambda code: exit_calls.append(code))
    api = Api()

    result = api.confirm_update("https://example.com/QuickRes.exe")

    assert result["ok"] is False
    assert "network is down" in result["message"]
    assert exit_calls == []

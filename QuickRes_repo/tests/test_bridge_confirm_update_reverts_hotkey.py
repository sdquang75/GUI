"""confirm_update() (bridge_op(lock=True)) can force-kill the process via
os._exit(0) (inside updater.confirm_update, reached after a successful
apply_update raises SystemExit) while a hotkey toggle is live and currently
stretched. os._exit terminates the process immediately, skipping normal
interpreter shutdown and therefore webview/app.py's _on_closing handler --
the only other code path that calls Api._stop_hotkey_impl to revert a
stretched display back to native on shutdown. The relaunched app has no
persisted memory of which resolution was active, so without a fix the
physical display stays stretched/visually broken after an update restarts,
with no automatic recovery.

Fix: confirm_update() now gives an active hotkey toggle the same best-effort
chance to revert that app.py's _on_closing already gives it -- when
self._hotkey_toggle is not None, it runs Api._stop_hotkey_impl on a
background thread bounded by _HOTKEY_REVERT_UPDATE_TIMEOUT_S before
delegating to updater.confirm_update, mirroring the pending-guard resolve
attempt already in this method.
"""
import threading

import pytest

from quickres.webview.bridge import Api
from quickres import config
from quickres.webview import bridge as bridge_module


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


class _FakeStretchedToggle:
    def __init__(self):
        self.native_res = (1920, 1080)
        self.is_stretched = True


class TestConfirmUpdateRevertsStretchedHotkeyBeforeDelegating:
    def test_active_stretched_toggle_gets_a_revert_attempt_before_updater_confirm_update(
        self, monkeypatch
    ):
        api = Api()
        api._hotkey_toggle = _FakeStretchedToggle()
        api._hotkey_running = True

        events = []
        observed = {}

        def _fake_stop_hotkey_impl():
            events.append("stop_hotkey")
            observed["thread"] = threading.current_thread()

        monkeypatch.setattr(api, "_stop_hotkey_impl", _fake_stop_hotkey_impl)

        def _fake_confirm_update(download_url, version_info=None):
            events.append("updater_confirm_update")
            return {"applied": True}

        monkeypatch.setattr(
            "quickres.webview.bridge.updater.confirm_update", _fake_confirm_update
        )

        join_timeouts = []
        original_join = threading.Thread.join

        def _tracking_join(self, timeout=None):
            join_timeouts.append(timeout)
            return original_join(self, timeout)

        monkeypatch.setattr(threading.Thread, "join", _tracking_join)

        result = api.confirm_update("https://example.com/x.exe")

        assert result["ok"] is True
        assert result["data"] == {"applied": True}
        assert events == ["stop_hotkey", "updater_confirm_update"], (
            "an active hotkey toggle must get a revert attempt BEFORE "
            "delegating onward to updater.confirm_update"
        )
        assert observed["thread"] is not threading.main_thread(), (
            "the revert attempt must run on a background thread bounded by "
            "a join timeout, mirroring the pending-guard resolve attempt -- "
            "not synchronously inline"
        )
        assert bridge_module._HOTKEY_REVERT_UPDATE_TIMEOUT_S in join_timeouts, (
            "the reverter thread must be joined with the expected bound"
        )
        assert api._op_lock.locked() is False, (
            "self._op_lock must end up released after confirm_update "
            "returns, same as any other bridge_op(lock=True) method"
        )

    def test_slow_revert_does_not_hang_the_update_flow(self, monkeypatch):
        monkeypatch.setattr(bridge_module, "_HOTKEY_REVERT_UPDATE_TIMEOUT_S", 0.05)

        api = Api()
        api._hotkey_toggle = _FakeStretchedToggle()
        api._hotkey_running = True

        import time as _time

        def _slow_stop_hotkey_impl():
            _time.sleep(0.5)

        monkeypatch.setattr(api, "_stop_hotkey_impl", _slow_stop_hotkey_impl)

        def _fake_confirm_update(download_url, version_info=None):
            return {"applied": True}

        monkeypatch.setattr(
            "quickres.webview.bridge.updater.confirm_update", _fake_confirm_update
        )

        started = _time.monotonic()
        result = api.confirm_update("https://example.com/x.exe")
        elapsed = _time.monotonic() - started

        assert result["ok"] is True
        assert elapsed < 0.3, (
            "a slow/stuck hotkey revert must not block confirm_update "
            "indefinitely -- the update must proceed once the bound elapses"
        )


class TestConfirmUpdateWithNoActiveHotkeyIsUnchanged:
    def test_no_hotkey_toggle_proceeds_directly_with_no_revert_attempt(self, monkeypatch):
        api = Api()
        assert api._hotkey_toggle is None

        events = []

        def _fake_confirm_update(download_url, version_info=None):
            events.append("updater_confirm_update")
            return {"applied": True}

        monkeypatch.setattr(
            "quickres.webview.bridge.updater.confirm_update", _fake_confirm_update
        )

        revert_calls = []
        monkeypatch.setattr(
            api, "_stop_hotkey_impl", lambda: revert_calls.append(True)
        )

        result = api.confirm_update("https://example.com/x.exe")

        assert result["ok"] is True
        assert result["data"] == {"applied": True}
        assert events == ["updater_confirm_update"]
        assert revert_calls == [], (
            "with no active hotkey toggle, confirm_update must proceed "
            "exactly as before -- no revert attempt at all"
        )
        assert api._op_lock.locked() is False

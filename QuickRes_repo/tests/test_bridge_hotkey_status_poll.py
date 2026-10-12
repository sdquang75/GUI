"""Round 24 finding (R4 Resilience, HIGH): a GetMessageW failure inside
`HotkeyToggle._run`'s Win32 message loop silently kills the listener thread
with NO automatic restart and NO UI-visible signal -- the failure is only
ever reported through `on_status`/`log_msg`, both quickres.log-only
channels. `self._hotkey_running` (bridge.py) is a separately-tracked flag
that only ever flips inside `start_hotkey`/`stop_hotkey` themselves, so it
keeps reporting True indefinitely after the listener thread has actually
died on its own.

`Api.get_hotkey_status` is a cheap, `recheck_pending`-style read-only poll:
it checks the toggle's OWN actual liveness (`HotkeyToggle.is_running`,
backed by `self._thread_id`) rather than trusting the possibly-stale
`self._hotkey_running` flag, correcting both `self._hotkey_running` and
`self._hotkey_toggle` when it finds the listener has died, so panel.html's
periodic poll (mirroring round 22's guardPollTimer pattern) can notice
within roughly one poll interval instead of never.
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


class _FakeToggle:
    def __init__(self, running: bool):
        self._running = running

    @property
    def is_running(self):
        return self._running


class TestGetHotkeyStatusReflectsRealListenerLiveness:
    def test_reports_running_true_while_the_listener_is_actually_alive(self):
        api = Api()
        api._hotkey_toggle = _FakeToggle(running=True)
        api._hotkey_running = True

        result = api.get_hotkey_status()

        assert result["ok"] is True
        assert result["data"]["running"] is True
        assert api._hotkey_running is True

    def test_detects_a_dead_listener_and_flips_running_to_false(self):
        # Simulates the GetMessageW failure scenario: self._hotkey_running
        # is still True (nothing ever called stop_hotkey), but the
        # listener's own thread has actually exited.
        api = Api()
        api._hotkey_toggle = _FakeToggle(running=False)
        api._hotkey_running = True

        result = api.get_hotkey_status()

        assert result["ok"] is True
        assert result["data"]["running"] is False, (
            "must report the listener's real liveness, not the stale "
            "self._hotkey_running flag"
        )
        assert api._hotkey_running is False, (
            "must correct the stale flag so later reads (and a fresh "
            "start_hotkey) see accurate state"
        )
        assert api._hotkey_toggle is None, (
            "the dead toggle reference must be cleared, not left dangling"
        )

    def test_reports_running_false_when_no_hotkey_was_ever_started(self):
        api = Api()

        result = api.get_hotkey_status()

        assert result["ok"] is True
        assert result["data"]["running"] is False

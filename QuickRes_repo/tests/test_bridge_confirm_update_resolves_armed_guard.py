"""confirm_update() (bridge_op(lock=True)) can force-kill the process via
os._exit(0) (inside updater.confirm_update, reached after a successful
apply_update raises SystemExit) while a monitor-disable's 10-second
auto-revert PendingDisableGuard (self._pending_guard) is still armed.
self._op_lock is released as soon as the original disable call returns (the
10s grace period is NOT held under the lock), so a user can disable a
monitor, then within that window click "Update Now", triggering
confirm_update -> updater.confirm_update -> os._exit(0). os._exit terminates
the process immediately, skipping normal interpreter shutdown and therefore
webview/app.py's _on_closing handler -- the only code path that otherwise
gives the still-armed guard a bounded, best-effort chance
(_resolve_guard_unbounded_under_lock, joined with a timeout) to resolve
before exit.

Fix: confirm_update() now mirrors app.py's _on_closing pattern itself --
when self._pending_guard is armed, it releases self._op_lock (already held
via bridge_op(lock=True); _resolve_guard_unbounded_under_lock needs to
acquire that same lock itself, exactly like _on_closing, which never holds
it either), resolves the guard on a background thread bounded by
_GUARD_RESOLVE_UPDATE_TIMEOUT_S, then reacquires the lock before delegating
to updater.confirm_update -- so bridge_op's own release() at the end of the
call still balances correctly.
"""
import threading

import pytest

from quickres.webview.bridge import Api
from quickres import config, monitors
from quickres.webview import bridge as bridge_module


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


def _api_with_confirmed_disable(monkeypatch):
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True}],
    )
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.set_monitors_enabled",
        lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
    )
    api = Api()
    api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
    return api


class TestConfirmUpdateResolvesArmedGuardBeforeDelegating:
    def test_armed_guard_gets_a_bounded_resolve_attempt_before_updater_confirm_update(
        self, monkeypatch
    ):
        api = _api_with_confirmed_disable(monkeypatch)
        guard = api._pending_guard
        assert guard is not None, "setup must arm a real pending guard"

        events = []
        observed = {}

        def _fake_resolve_guard(g, now=None):
            events.append("resolve_guard")
            observed["locked_during_resolve"] = api._op_lock.locked()
            observed["thread"] = threading.current_thread()
            assert g is guard

        monkeypatch.setattr(api, "_resolve_guard_unbounded_under_lock", _fake_resolve_guard)

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
        assert events == ["resolve_guard", "updater_confirm_update"], (
            "the armed guard must get a resolve attempt BEFORE delegating "
            "onward to updater.confirm_update"
        )
        assert observed["locked_during_resolve"] is False, (
            "self._op_lock must be released while the guard-resolve attempt "
            "runs -- otherwise _resolve_guard_unbounded_under_lock's own "
            "unbounded acquire of that same lock can never succeed while "
            "confirm_update's bridge_op(lock=True) is still holding it, and "
            "the resolve attempt would be a guaranteed no-op"
        )
        assert observed["thread"] is not threading.main_thread(), (
            "the resolve attempt must run on a background thread bounded by "
            "a join timeout, mirroring app.py's _on_closing pattern -- not "
            "synchronously inline"
        )
        assert bridge_module._GUARD_RESOLVE_UPDATE_TIMEOUT_S in join_timeouts, (
            "the resolver thread must be joined with the expected bound"
        )
        assert api._op_lock.locked() is False, (
            "self._op_lock must end up released after confirm_update "
            "returns, same as any other bridge_op(lock=True) method"
        )


class TestConfirmUpdateWithNoArmedGuardIsUnchanged:
    def test_no_guard_proceeds_directly_with_no_resolve_attempt(self, monkeypatch):
        api = Api()
        assert api._pending_guard is None

        events = []

        def _fake_confirm_update(download_url, version_info=None):
            events.append("updater_confirm_update")
            return {"applied": True}

        monkeypatch.setattr(
            "quickres.webview.bridge.updater.confirm_update", _fake_confirm_update
        )

        resolve_calls = []
        monkeypatch.setattr(
            api, "_resolve_guard_unbounded_under_lock",
            lambda *a, **k: resolve_calls.append((a, k)),
        )

        result = api.confirm_update("https://example.com/x.exe")

        assert result["ok"] is True
        assert result["data"] == {"applied": True}
        assert events == ["updater_confirm_update"]
        assert resolve_calls == [], (
            "with no armed guard, confirm_update must proceed exactly as "
            "before -- no guard-resolve attempt at all"
        )
        assert api._op_lock.locked() is False

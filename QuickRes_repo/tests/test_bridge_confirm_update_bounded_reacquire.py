"""confirm_update()'s guard-resolve path used to bound only the
`resolver.join(_GUARD_RESOLVE_UPDATE_TIMEOUT_S)` wait for the background
resolver thread -- the very next line then did a bare, unbounded
`self._op_lock.acquire()`. That reacquire only actually blocks when the
resolver thread is still running and still holds `self._op_lock` (e.g. a
UAC prompt for the auto-revert has not been answered yet), which
`guard.check()` -> `monitors.set_monitors_enabled` can legitimately do for
up to its own ~30s helper-wait default -- defeating the whole point of
bounding the earlier `join()` call and leaving confirm_update able to hang
for up to ~30s with no UI feedback, contradicting its own docstring's
"cannot hang the update flow indefinitely" claim.

Fix: the reacquire is now ALSO bounded (by the same
`_GUARD_RESOLVE_UPDATE_TIMEOUT_S`), so the total extra wait confirm_update
can impose is capped at roughly twice that constant. When the bounded
reacquire itself fails, confirm_update signals this to `bridge_op` (via the
`_LockReacquireFailed` exception `bridge_op` special-cases) instead of
proceeding into `updater.confirm_update` -- `bridge_op`'s `finally` block
then correctly skips its own `self._op_lock.release()`, since this thread
never got the lock back and releasing it would either error (release of an
unlocked lock) or, worse, silently release a lock some OTHER thread (the
still-running resolver) legitimately holds. The caller gets the same
kind="busy" envelope every other lock=True method returns for "could not
get the lock right now".
"""
import threading
import time

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


def _fake_slow_resolve_guard(api, release_event, holding_event=None):
    """Stands in for `_resolve_guard_unbounded_under_lock`: simulates a
    resolver that legitimately keeps running (e.g. an unanswered UAC
    prompt) well past both of confirm_update's bounds, actually holding
    `api._op_lock` for that whole time -- exactly the scenario that used to
    force confirm_update's old unbounded reacquire to wait as long as the
    real helper-wait default allows.
    """
    def _fake(g, now=None):
        api._op_lock.acquire()
        if holding_event is not None:
            holding_event.set()
        release_event.wait(timeout=5)
        api._op_lock.release()
    return _fake


class TestConfirmUpdateBoundedReacquireOnSlowGuardResolve:
    def test_total_wait_is_bounded_and_returns_busy_without_calling_updater(self, monkeypatch):
        monkeypatch.setattr(bridge_module, "_GUARD_RESOLVE_UPDATE_TIMEOUT_S", 0.2)

        api = _api_with_confirmed_disable(monkeypatch)
        guard = api._pending_guard
        assert guard is not None, "setup must arm a real pending guard"

        holding = threading.Event()
        release_fake_lock = threading.Event()
        monkeypatch.setattr(
            api, "_resolve_guard_unbounded_under_lock",
            _fake_slow_resolve_guard(api, release_fake_lock, holding),
        )

        updater_calls = []
        monkeypatch.setattr(
            "quickres.webview.bridge.updater.confirm_update",
            lambda *a, **k: updater_calls.append((a, k)) or {"applied": True},
        )

        started = time.monotonic()
        result = api.confirm_update("https://example.com/x.exe")
        elapsed = time.monotonic() - started

        # The total extra wait confirm_update can impose is the join bound
        # plus the reacquire bound -- roughly 2 * _GUARD_RESOLVE_UPDATE_TIMEOUT_S
        # (0.4s here), nowhere near the ~30s the old unbounded reacquire
        # could allow when the resolver legitimately keeps the lock.
        assert elapsed < 2.0, (
            f"confirm_update took {elapsed:.2f}s -- the reacquire must be "
            "bounded, not an unbounded wait for the still-running resolver"
        )
        assert result["ok"] is False
        assert result["kind"] == "busy"
        assert updater_calls == [], (
            "confirm_update must not proceed to updater.confirm_update when "
            "it could not safely reacquire the lock in time"
        )

        assert holding.wait(timeout=2), "the fake resolver never actually took the lock"
        release_fake_lock.set()

    def test_lock_state_is_correct_for_a_competing_call_after_giveup(self, monkeypatch):
        monkeypatch.setattr(bridge_module, "_GUARD_RESOLVE_UPDATE_TIMEOUT_S", 0.2)

        api = _api_with_confirmed_disable(monkeypatch)
        guard = api._pending_guard
        assert guard is not None, "setup must arm a real pending guard"

        holding = threading.Event()
        release_fake_lock = threading.Event()
        monkeypatch.setattr(
            api, "_resolve_guard_unbounded_under_lock",
            _fake_slow_resolve_guard(api, release_fake_lock, holding),
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.updater.confirm_update",
            lambda *a, **k: {"applied": True},
        )

        result = api.confirm_update("https://example.com/x.exe")
        assert result["kind"] == "busy"
        assert holding.is_set(), "the fake resolver never actually took the lock"

        # The background resolver thread is still legitimately holding
        # self._op_lock at this point. bridge_op's `finally` must NOT have
        # released a lock this (main) thread never got back -- doing so
        # would either error (releasing an unlocked lock, if the resolver
        # had already finished) or, as reproduced here, silently release a
        # lock the resolver thread still actively holds.
        assert api._op_lock.locked() is True, (
            "the lock the background resolver thread legitimately holds "
            "must still be held -- bridge_op's finally must not have "
            "released a lock this thread doesn't own"
        )

        # A competing lock=True call right now must correctly observe the
        # lock as busy, not silently succeed because bridge_op's finally
        # wrongly freed a lock it never actually held.
        competing_result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)
        assert competing_result["ok"] is False
        assert competing_result["kind"] == "busy"

        release_fake_lock.set()
        deadline = time.monotonic() + 2
        while api._op_lock.locked() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert api._op_lock.locked() is False, "the fake resolver must eventually release the lock"


class TestConfirmUpdateHappyPathUnaffectedByBoundedReacquire:
    def test_no_guard_still_proceeds_directly_with_no_resolve_attempt(self, monkeypatch):
        api = Api()
        assert api._pending_guard is None

        monkeypatch.setattr(
            "quickres.webview.bridge.updater.confirm_update",
            lambda *a, **k: {"applied": True},
        )

        result = api.confirm_update("https://example.com/x.exe")

        assert result["ok"] is True
        assert result["data"] == {"applied": True}
        assert api._op_lock.locked() is False

    def test_guard_resolving_promptly_still_proceeds_to_updater_confirm_update(self, monkeypatch):
        api = _api_with_confirmed_disable(monkeypatch)
        guard = api._pending_guard
        assert guard is not None, "setup must arm a real pending guard"

        # Resolves promptly and never touches the lock itself, mirroring
        # the real method's own release-then-eventually-release-again
        # behavior once its own unbounded internal acquire succeeds fast.
        monkeypatch.setattr(
            api, "_resolve_guard_unbounded_under_lock", lambda g, now=None: None,
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.updater.confirm_update",
            lambda *a, **k: {"applied": True},
        )

        result = api.confirm_update("https://example.com/x.exe")

        assert result["ok"] is True
        assert result["data"] == {"applied": True}
        assert api._op_lock.locked() is False

"""Round 21 finding 1 (readability, defense-in-depth): `_resolve_pending_now`'s
own docstring says it must NOT be called without `self._op_lock` already
held -- its two legitimate callers (`recheck_pending`, `force_unlock_pending`)
rely on `bridge_op(lock=True)` to hold it for them first. Nothing in the code
enforced that precondition; a future caller reaching this plainer-named
method directly from an unlocked context would silently reintroduce the
exact race the on-disk result-file read was written to avoid (`monitors.
read_op_result` destructively deletes the result file on first read).

Fix: a lightweight `assert self._op_lock.locked()` at the top of
`_resolve_pending_now` turns that silent misuse into a loud AssertionError
at the exact call site.
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


class TestResolvePendingNowRequiresTheLockAlreadyHeld:
    def test_raises_assertion_error_when_called_without_the_lock_held(self):
        api = Api()
        assert not api._op_lock.locked()

        with pytest.raises(AssertionError):
            api._resolve_pending_now()

    def test_works_normally_when_called_with_the_lock_already_held(self):
        api = Api()
        api._op_lock.acquire()
        try:
            outcomes = api._resolve_pending_now()
        finally:
            api._op_lock.release()

        assert outcomes == []

    def test_recheck_pending_still_works_via_bridge_op_lock_true(self):
        # recheck_pending is bridge_op(lock=True) -- it acquires
        # self._op_lock itself before its body (and thus before
        # _resolve_pending_now) ever runs.
        api = Api()

        result = api.recheck_pending()

        assert result["ok"] is True
        assert result["data"]["outcomes"] == []

    def test_force_unlock_pending_still_raises_its_own_error_not_an_assertion(self):
        # Nothing pending -- force_unlock_pending should still reach its own
        # "Nothing to force-unlock" RuntimeError via the normal bridge_op
        # envelope, not blow up on the new assertion (bridge_op(lock=True)
        # already holds self._op_lock by the time the body runs).
        api = Api()

        result = api.force_unlock_pending()

        assert result["ok"] is False
        assert result["kind"] == "error"
        assert "force-unlock" in result["message"].lower()

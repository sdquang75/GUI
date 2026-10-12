"""Round 13 finding (HIGH): `confirm_update` was decorated with only
`@bridge_op()` (no `lock=True`), unlike every other state-mutating method
(`set_monitors_enabled`, `keep_disabled`, `revert_now`,
`force_unlock_pending`). A user double-clicking "Update Now" while a slow
download is in flight could fire two concurrent `confirm_update` calls,
both proceeding into `updater.confirm_update()` -> `updater.apply_update()`
at once -- racing over the same fixed on-disk paths
(`QuickRes_new.exe`, `update.bat`) that `apply_update()` writes/reads with
no locking of its own.

`confirm_update` now carries `lock=True`, reusing the same `self._op_lock`
`bridge_op` already uses to serialize `set_monitors_enabled`/
`keep_disabled`/`revert_now`/`force_unlock_pending` -- a second concurrent
call must see `kind="busy"` instead of racing into `updater.confirm_update`.
"""
import threading

import pytest

from quickres.webview.bridge import Api
from quickres import config


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


class TestConcurrentConfirmUpdateCallsAreSerialized:
    def test_second_call_gets_busy_while_first_is_still_applying(self, monkeypatch):
        api = Api()
        entered = threading.Event()
        release = threading.Event()

        def _slow_confirm_update(download_url, version_info=None):
            entered.set()
            release.wait(timeout=1)
            return {"applied": True}

        monkeypatch.setattr(
            "quickres.webview.bridge.updater.confirm_update", _slow_confirm_update
        )

        first_result = {}

        def _first_call():
            first_result["value"] = api.confirm_update("https://example.com/x.exe")

        t1 = threading.Thread(target=_first_call)
        t1.start()
        assert entered.wait(timeout=2), "first call never reached updater.confirm_update"

        second_result = api.confirm_update("https://example.com/x.exe")

        assert second_result["ok"] is False
        assert second_result["kind"] == "busy", (
            "a second concurrent confirm_update call raced into "
            "updater.confirm_update instead of being serialized by "
            "self._op_lock"
        )

        release.set()
        t1.join(timeout=2)

        assert not t1.is_alive()
        assert first_result["value"]["ok"] is True
        assert first_result["value"]["data"] == {"applied": True}

"""Round 10 finding 2: `recover_on_boot`'s IN_FLIGHT branch called
`self._op_lock.acquire(blocking=False)` and discarded its return value --
if the acquire failed (the lock already held by something else at that
exact moment), the pre-fix code still set `self._boot_armed = True` as if
this call genuinely held the lock. Every other lock-acquisition site in
this file checks the return value; this was the one exception, and it
undermined the whole `_boot_armed`/lock-leak safety model -- `bridge_op`'s
boot-armed-bypass logic assumes `_boot_armed` implies the lock is genuinely
held by this recovery path.

Fix: the acquire's return value is now checked. `self._boot_armed` is only
set `True` when the acquire genuinely succeeded; on failure the anomaly is
logged via `log_msg` and `_boot_armed` is left `False`, keeping the
bypass logic's invariant honest.
"""
import pytest

from quickres.webview.bridge import Api
from quickres import config, recovery


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


def _in_flight_outcome():
    return recovery.PendingOutcome(
        resolution=recovery.Resolution.IN_FLIGHT,
        instance_id="DISPLAY\\A\\1", friendly_name="A",
        message="Still resolving", elapsed_s=1.0, can_force_unlock=False,
    )


class TestRecoverOnBootHonorsFailedLockAcquire:
    def test_boot_armed_stays_false_when_lock_already_held(self, monkeypatch):
        monkeypatch.setattr(
            Api, "_resolve_pending_now_bounded_under_lock", lambda self: [_in_flight_outcome()]
        )
        logged = []
        monkeypatch.setattr("quickres.webview.bridge.log_msg", lambda msg: logged.append(msg))
        api = Api()
        # Simulate the lock genuinely held by something else at this exact
        # moment -- a real, non-reentrant threading.Lock, so a second
        # acquire attempt from within recover_on_boot legitimately fails.
        api._op_lock.acquire(blocking=False)

        result = api.recover_on_boot()

        assert api._boot_armed is False
        assert any("recover_on_boot" in msg or "lock" in msg.lower() for msg in logged), (
            f"expected an anomaly log message, got: {logged!r}"
        )
        assert result["outcomes"]

    def test_boot_armed_set_true_when_lock_genuinely_acquired(self, monkeypatch):
        monkeypatch.setattr(
            Api, "_resolve_pending_now_bounded_under_lock", lambda self: [_in_flight_outcome()]
        )
        logged = []
        monkeypatch.setattr("quickres.webview.bridge.log_msg", lambda msg: logged.append(msg))
        api = Api()

        result = api.recover_on_boot()

        assert api._boot_armed is True
        assert api._op_lock.locked() is True
        assert logged == []
        assert result["outcomes"]

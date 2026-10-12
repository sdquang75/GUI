"""R3 Reliability finding (CRITICAL): `bridge_op`'s lock-release logic only
releases `self._op_lock`/`self._boot_armed` on the success path of a
`boot_armed_bypass` call. `_check_no_in_flight_pending` (used by
`keep_disabled`/`revert_now`) and `force_unlock_pending`'s own inline
IN_FLIGHT guard both raise a plain exception to report "genuinely still in
flight, cannot act yet" -- that raise used to be indistinguishable, at the
`bridge_op` boundary, from an actual bug in the method body. These tests
prove the "still in flight" rejection is reported as a transient `busy`
outcome (not a hard `error`) and, more importantly, that it never leaves
`self._op_lock`/`self._boot_armed` any less recoverable than they already
were: a LATER call, once the underlying operation genuinely resolves, still
succeeds and releases normally.
"""
import pytest

from quickres.webview.bridge import Api, _InFlightStillPending
from quickres import config, recovery


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


def _boot_armed_api():
    api = Api()
    api._op_lock.acquire(blocking=False)
    api._lock_reason = "A monitor operation from a previous session is still resolving"
    api._boot_armed = True
    return api


def _in_flight_outcome():
    return recovery.PendingOutcome(
        resolution=recovery.Resolution.IN_FLIGHT,
        instance_id="DISPLAY\\A\\1", friendly_name="A",
        message="still running", elapsed_s=5.0, can_force_unlock=False,
    )


def _resolver_sequence(*batches):
    """Returns a fake `_resolve_pending_now` that yields each batch in
    `batches` in order on successive calls, repeating the last one forever
    once exhausted.
    """
    calls = {"n": 0}

    def _fake(self):
        idx = min(calls["n"], len(batches) - 1)
        calls["n"] += 1
        return batches[idx]

    return _fake


class TestCheckNoInFlightPendingRaisesDedicatedType:
    def test_raises_in_flight_still_pending_not_a_bare_runtime_error(self, monkeypatch):
        api = Api()
        api._op_lock.acquire(blocking=False)
        monkeypatch.setattr(Api, "_resolve_pending_now", lambda self: [_in_flight_outcome()])

        with pytest.raises(_InFlightStillPending):
            api._check_no_in_flight_pending()

        api._op_lock.release()


class TestKeepDisabledInFlightRejectionIsRecoverable:
    def test_reports_busy_and_leaves_lock_and_boot_armed_unchanged(self, monkeypatch):
        api = _boot_armed_api()
        monkeypatch.setattr(
            Api, "_resolve_pending_now", _resolver_sequence([_in_flight_outcome()])
        )

        result = api.keep_disabled()

        assert result["ok"] is False
        assert result["kind"] == "busy"
        assert api._op_lock.locked() is True
        assert api._boot_armed is True

    def test_succeeds_and_releases_once_the_operation_later_resolves(self, monkeypatch):
        api = _boot_armed_api()
        monkeypatch.setattr(
            Api, "_resolve_pending_now",
            _resolver_sequence([_in_flight_outcome()], []),
        )

        rejected = api.keep_disabled()
        assert rejected["kind"] == "busy"

        resolved = api.keep_disabled()

        assert resolved["ok"] is True
        assert api._op_lock.locked() is False
        assert api._boot_armed is False

    def test_other_lock_true_operations_recover_after_the_condition_resolves(self, monkeypatch):
        api = _boot_armed_api()
        monkeypatch.setattr(
            Api, "_resolve_pending_now",
            _resolver_sequence([_in_flight_outcome()], []),
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled: [(iid, True, "ok") for iid in ids],
        )

        api.keep_disabled()  # rejected -- still in flight
        still_busy = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)
        assert still_busy["kind"] == "busy"

        api.keep_disabled()  # now resolves and releases

        recovered = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)
        assert recovered["ok"] is True
        assert recovered["kind"] == "ok"


class TestRevertNowInFlightRejectionIsRecoverable:
    def test_reports_busy_and_leaves_lock_and_boot_armed_unchanged(self, monkeypatch):
        api = _boot_armed_api()
        monkeypatch.setattr(
            Api, "_resolve_pending_now", _resolver_sequence([_in_flight_outcome()])
        )

        result = api.revert_now()

        assert result["ok"] is False
        assert result["kind"] == "busy"
        assert api._op_lock.locked() is True
        assert api._boot_armed is True

    def test_succeeds_and_releases_once_the_operation_later_resolves(self, monkeypatch):
        api = _boot_armed_api()
        monkeypatch.setattr(
            Api, "_resolve_pending_now",
            _resolver_sequence([_in_flight_outcome()], []),
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled: [],
        )

        rejected = api.revert_now()
        assert rejected["kind"] == "busy"

        resolved = api.revert_now()

        assert resolved["ok"] is True
        assert api._op_lock.locked() is False
        assert api._boot_armed is False


class TestForceUnlockPendingInFlightRejectionIsRecoverable:
    def test_reports_busy_and_leaves_lock_and_boot_armed_unchanged(self, monkeypatch):
        api = _boot_armed_api()
        monkeypatch.setattr(
            Api, "_resolve_pending_now", _resolver_sequence([_in_flight_outcome()])
        )

        result = api.force_unlock_pending()

        assert result["ok"] is False
        assert result["kind"] == "busy"
        assert api._op_lock.locked() is True
        assert api._boot_armed is True

    def test_succeeds_and_releases_once_the_operation_later_resolves(self, monkeypatch):
        unconfirmable_outcome = recovery.PendingOutcome(
            resolution=recovery.Resolution.UNCONFIRMABLE,
            instance_id="DISPLAY\\A\\1", friendly_name="A",
            message="Could not confirm", elapsed_s=200.0, can_force_unlock=True,
        )
        api = _boot_armed_api()
        monkeypatch.setattr(
            Api, "_resolve_pending_now",
            _resolver_sequence([_in_flight_outcome()], [unconfirmable_outcome]),
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.config.load_pending",
            lambda: {"action": "disable", "targets": [{"instance_id": "DISPLAY\\A\\1"}]},
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.config.save_pending", lambda record: True
        )

        rejected = api.force_unlock_pending()
        assert rejected["kind"] == "busy"

        resolved = api.force_unlock_pending()

        assert resolved["ok"] is True
        assert api._op_lock.locked() is False
        assert api._boot_armed is False


class TestInFlightRejectionOutsideBootArmedContext:
    def test_normal_lock_path_still_releases_the_lock_it_freshly_acquired(self, monkeypatch):
        # guard is None but self._boot_armed is False here -- a plain
        # lock=True acquire, not the boot-armed bypass. The dedicated
        # exception type must not disturb the ordinary release path bridge_op
        # already runs for every ok=False outcome.
        api = Api()
        monkeypatch.setattr(
            Api, "_resolve_pending_now", _resolver_sequence([_in_flight_outcome()])
        )

        result = api.keep_disabled()

        assert result["ok"] is False
        assert result["kind"] == "busy"
        assert api._op_lock.locked() is False

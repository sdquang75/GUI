"""Round 8 finding (Stream 2, MEDIUM): retrying a disable for a target that
already has an unresolved entry in the on-disk crash-recovery record used to
launch a SECOND concurrent elevated helper against the same device instance
while the original helper's liveness was genuinely UNKNOWN (not confirmed
dead) -- two CM_Disable_DevNode/CM_Enable_DevNode calls racing the same
devnode. `_build_and_save_pending_record`'s merge also overwrites the
record's single global `helper_pid`/`result_file` fields with the new
batch's, permanently losing the ability to reconcile the original attempt.

`Api._check_no_stale_record_conflict` closes this: a retry of a target
already pending on disk is refused unless `monitors.process_liveness` on the
existing record's helper_pid/owner_pid resolves to DEAD.
"""
import pytest

from quickres.webview.bridge import Api
from quickres import config, monitors, recovery


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


def _known_monitor(monkeypatch, instance_id="DISPLAY\\A\\1", friendly_name="A"):
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [{"instance_id": instance_id, "friendly_name": friendly_name, "enabled": True}],
    )


def _arm_timed_out_pending_target(api, monkeypatch, instance_id="DISPLAY\\A\\1", helper_pid=1111):
    """First disable of `instance_id`: times out, records `helper_pid` via
    the real on_helper_launched wiring, leaves an unresolved on-disk entry.
    """
    def _timeout_with_pid(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
        if on_helper_launched:
            on_helper_launched(helper_pid)
        return [(iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids]

    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.set_monitors_enabled", _timeout_with_pid
    )
    result = api.set_monitors_enabled([instance_id], False)
    assert result["ok"] is True
    assert api._pending_guard is None
    record = config.load_pending()
    assert record is not None
    assert record["helper_pid"] == helper_pid


class TestRetryRefusedWhileOriginalHelperMightStillBeAlive:
    @pytest.mark.parametrize("liveness", [recovery.Liveness.ALIVE, recovery.Liveness.UNKNOWN])
    def test_retry_of_same_target_refused_when_liveness_not_confirmed_dead(
        self, monkeypatch, liveness
    ):
        _known_monitor(monkeypatch)
        api = Api()
        _arm_timed_out_pending_target(api, monkeypatch)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness", lambda *a, **k: liveness
        )
        launched = {"called": False}

        def _fail_if_launched(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
            launched["called"] = True
            return [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fail_if_launched
        )

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is False
        assert result["kind"] == "error"
        assert launched["called"] is False, (
            "must not launch a second concurrent helper while the original "
            "helper's liveness is not confirmed dead"
        )
        # The stale record (including the original helper_pid) must survive
        # the refused retry so it can still be reconciled later.
        record = config.load_pending()
        assert record["helper_pid"] == 1111

    def test_retry_of_a_different_unrelated_target_is_unaffected(self, monkeypatch):
        # Same scoping guarantee as _check_no_live_guard_conflict: this
        # check must not block an unrelated target just because a DIFFERENT
        # target is pending with unresolved liveness.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
                {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B", "enabled": True},
            ],
        )
        api = Api()
        _arm_timed_out_pending_target(api, monkeypatch)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness",
            lambda *a, **k: recovery.Liveness.ALIVE,
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )

        result = api.set_monitors_enabled(["DISPLAY\\B\\2"], False)

        assert result["ok"] is True


class TestRetryProceedsOnceLivenessConfirmedDead:
    def test_retry_of_same_target_proceeds_when_liveness_confirmed_dead(self, monkeypatch):
        _known_monitor(monkeypatch)
        api = Api()
        _arm_timed_out_pending_target(api, monkeypatch)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness",
            lambda *a, **k: recovery.Liveness.DEAD,
        )
        launched = {"called": False}

        def _fresh_helper(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
            launched["called"] = True
            if on_helper_launched:
                on_helper_launched(2222)
            return [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fresh_helper
        )

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        assert launched["called"] is True
        # The retry's own fresh helper now owns the on-disk record (a
        # confirmed disable arms a 10s auto-revert guard, so the record
        # itself is not cleared yet -- same as any ordinary confirmed
        # disable -- but it must reflect the NEW helper, not the stale one).
        record = config.load_pending()
        assert record is not None
        assert record["helper_pid"] == 2222
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}

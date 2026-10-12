"""Round 6 corrective fixes (Stream 1: bridge.py + monitors.py), items 2-3:

2. HIGH: revert_now() used to clear/trim the on-disk pending record
   unconditionally after calling set_monitors_enabled(target_ids, True),
   regardless of whether the returned per-id results actually indicated
   success. A target whose revert genuinely failed (ok=False) must keep its
   crash-recovery entry rather than being trimmed on the strength of "the
   call didn't raise" or "some OTHER target in the same guard succeeded".

3. MEDIUM: _finalize_disable_outcome() never trimmed a genuinely-failed
   (non-timeout) target out of pending_restore.json when it was mixed with
   a confirmed success in the same batch disable -- the failed target's
   stale entry stuck around for the rest of the session.
"""
import pytest

from quickres.webview.bridge import Api
from quickres import config, monitors


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


class _NoOpTimer:
    """A revert_now() call whose re-enable attempt fails now leaves its
    guard armed and may schedule a bounded auto-retry timer (see
    tests/test_bridge_revert_now_failure_retry.py) -- this file's own tests
    only check the immediate, synchronous outcome of that call and never
    fire a timer callback themselves, so a real `threading.Timer` here would
    just be a stray background thread outliving the test. Swapped in for
    every test in this file so none of them ever start one.
    """

    def __init__(self, interval, function):
        pass

    def start(self):
        pass

    def cancel(self):
        pass


@pytest.fixture(autouse=True)
def _no_real_timers(monkeypatch):
    monkeypatch.setattr("quickres.webview.bridge.threading.Timer", _NoOpTimer)
    yield


class TestFinalizeDisableOutcomeTrimsMixedGenuineFailure:
    def test_genuine_failure_mixed_with_success_is_trimmed_not_left_stale(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
                {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B", "enabled": True},
            ],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                ("DISPLAY\\A\\1", True, "Disabled", monitors.OUTCOME_CONFIRMED),
                ("DISPLAY\\B\\2", False, "Elevation was cancelled or failed",
                 monitors.OUTCOME_GENUINE_FAILURE),
            ],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1", "DISPLAY\\B\\2"], False)

        assert result["ok"] is True
        record = config.load_pending()
        assert record is not None, "A's still-pending guard entry must survive"
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}, (
            "B genuinely failed (not a timeout) -- its stale entry must be "
            "trimmed immediately, not left stuck forever"
        )
        assert api._pending_guard.target_ids == ["DISPLAY\\A\\1"]

    def test_genuine_failure_mixed_with_timeout_trims_only_the_failure(self, monkeypatch):
        # Three-way mix: A fails genuinely, B times out (unknown, must
        # survive), nothing confirmed -- only A gets trimmed.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
                {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B", "enabled": True},
            ],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                ("DISPLAY\\A\\1", False, "Elevated helper did not report a result",
                 monitors.OUTCOME_GENUINE_FAILURE),
                ("DISPLAY\\B\\2", False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS),
            ],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1", "DISPLAY\\B\\2"], False)

        assert result["ok"] is True
        record = config.load_pending()
        assert record is not None
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\B\\2"}
        assert api._pending_guard is None, "no confirmed id -- no guard should be armed"

    def test_ambiguous_no_result_and_unconfirmed_state_is_not_trimmed(self, monkeypatch):
        # Round 13 R4 finding: a helper that completed but failed to persist
        # its own result file, combined with an unconfirmed fresh
        # device-state re-check, is an unknown outcome -- not a confirmed
        # failure. It must be treated the same as monitors.TIMEOUT_MESSAGE
        # here: left in place in pending_restore.json for recover_on_boot,
        # not trimmed, since its true state might still be a success.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
            ],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                ("DISPLAY\\A\\1", False, monitors.HELPER_RESULT_UNCONFIRMED_MESSAGE,
                 monitors.OUTCOME_AMBIGUOUS),
            ],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        record = config.load_pending()
        assert record is not None, "ambiguous outcome must survive, not be trimmed"
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}
        assert api._pending_guard is None, (
            "an unconfirmed outcome must not be treated as a confirmed success either"
        )

    def test_observed_mismatch_after_helper_success_is_not_trimmed(self, monkeypatch):
        # monitors.HELPER_OBSERVED_MISMATCH_MESSAGE means the helper's
        # CM_Disable_DevNode call reported success but the fresh observed
        # device state disagreed -- an unconfirmed outcome, not a genuine
        # failure. It must be left in place in pending_restore.json the
        # same way TIMEOUT_MESSAGE and HELPER_RESULT_UNCONFIRMED_MESSAGE
        # already are, matching recovery.resolve_pending's identical
        # UNCONFIRMABLE scoring of this same condition.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
            ],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                ("DISPLAY\\A\\1", False, monitors.HELPER_OBSERVED_MISMATCH_MESSAGE,
                 monitors.OUTCOME_AMBIGUOUS),
            ],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        record = config.load_pending()
        assert record is not None, "ambiguous outcome must survive, not be trimmed"
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}
        assert api._pending_guard is None, (
            "an unconfirmed outcome must not be treated as a confirmed success either"
        )


class TestRevertNowOnlyTrimsGenuinelySucceededTargets:
    def _two_monitor_disable(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
                {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B", "enabled": True},
            ],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1", "DISPLAY\\B\\2"], False)
        return api

    def test_partial_revert_failure_keeps_the_failed_targets_entry(self, monkeypatch):
        api = self._two_monitor_disable(monkeypatch)
        assert api._pending_guard.target_ids == ["DISPLAY\\A\\1", "DISPLAY\\B\\2"]

        def _fake_reenable(ids, enabled, **kwargs):
            # A genuinely re-enables; B reports a real failure, not a raise.
            return [
                ("DISPLAY\\A\\1", True, "Monitor enabled successfully", monitors.OUTCOME_CONFIRMED),
                ("DISPLAY\\B\\2", False, "Failed to enable monitor (CONFIGRET error 42)",
                 monitors.OUTCOME_GENUINE_FAILURE),
            ]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fake_reenable
        )

        result = api.revert_now()

        assert result["ok"] is True
        record = config.load_pending()
        assert record is not None, (
            "B's revert genuinely failed -- its crash-recovery entry must "
            "survive, never trimmed just because the call didn't raise"
        )
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\B\\2"}

    def test_full_revert_failure_leaves_the_whole_record_intact(self, monkeypatch):
        api = self._two_monitor_disable(monkeypatch)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, "Elevation was cancelled or failed", monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ],
        )

        result = api.revert_now()

        assert result["ok"] is True
        record = config.load_pending()
        assert record is not None, (
            "every target's revert genuinely failed -- nothing must be "
            "trimmed on the strength of 'set_monitors_enabled did not raise'"
        )
        assert {t["instance_id"] for t in record["targets"]} == {
            "DISPLAY\\A\\1", "DISPLAY\\B\\2",
        }

    def test_full_revert_success_still_fully_clears(self, monkeypatch):
        api = self._two_monitor_disable(monkeypatch)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )

        result = api.revert_now()

        assert result["ok"] is True
        assert config.load_pending() is None


class TestAutoRevertUnderLockOnlyTrimsGenuinelySucceededTargets:
    def test_partial_auto_revert_failure_keeps_the_failed_targets_entry(self, monkeypatch):
        import time

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
                {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B", "enabled": True},
            ],
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1", "DISPLAY\\B\\2"], False)
        guard = api._pending_guard

        def _fake_reenable(ids, enabled, **kwargs):
            return [
                ("DISPLAY\\A\\1", True, "ok", monitors.OUTCOME_CONFIRMED),
                ("DISPLAY\\B\\2", False, "still disabled", monitors.OUTCOME_GENUINE_FAILURE),
            ]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fake_reenable
        )

        future = time.time() + 3600
        api._resolve_guard_unbounded_under_lock(guard, now=future)

        assert guard.resolved is False, "B genuinely failed -- the guard is not fully resolved"
        record = config.load_pending()
        assert record is not None
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\B\\2"}

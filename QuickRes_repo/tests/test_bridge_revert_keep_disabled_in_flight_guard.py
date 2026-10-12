"""Round 19 R3 Reliability finding (HIGH): revert_now() and keep_disabled()
used to act on the boot/crash-recovered on-disk pending_restore.json record
with NO liveness/staleness check, unlike force_unlock_pending() (which
already refuses via `any(o.resolution == Resolution.IN_FLIGHT ...)`) and
_check_no_stale_record_conflict() (which already refuses a new disable
whenever an existing target's helper liveness isn't confirmed DEAD).

Failure chain: QuickRes disables a monitor, then the owning process
crashes/is killed while the elevated helper (an independent OS process
launched via ShellExecuteExW 'runas', not a child) is still genuinely
running its own CM_Disable_DevNode call. On relaunch, owner_pid on disk no
longer matches os.getpid(), so monitors.process_liveness() reports UNKNOWN
for the owner side -- never DEAD, never ALIVE -- and while elapsed_s is
still under the 120s expiry window, recovery.resolve_pending() classifies
the target IN_FLIGHT. recover_on_boot() re-arms self._op_lock with
self._boot_armed=True, and panel.html's pending card renders Keep/Revert
buttons regardless of the true per-target resolution. Clicking either used
to run unguarded: revert_now() would launch a BRAND NEW elevated helper to
re-enable the same device instance the still-running orphaned helper may be
mid-disable on, then trim/clear the crash-recovery record on a merely
momentary agreement -- leaving no on-disk trace if the orphaned helper wins
the race moments later and re-disables the monitor. keep_disabled() would
discard the record outright while the same hazard is live.

Fix: both methods now refuse (RuntimeError) instead of acting whenever the
on-disk record -- re-resolved via the same `_resolve_pending_now()` ladder
force_unlock_pending() already uses -- classifies as
Resolution.IN_FLIGHT, but ONLY for the boot/crash-recovered path (no live
`self._pending_guard` object in this process; see `_check_no_in_flight_pending`
in bridge.py for why a live in-process guard can never legitimately reach
IN_FLIGHT). A genuinely UNCONFIRMABLE or DEAD-helper on-disk record --
the normal/expected boot-recovery scenario these two methods exist to
resolve -- must keep working exactly as before.
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


def _write_in_flight_record(target_id="DISPLAY\\A\\1", friendly_name="A"):
    """A record that resolves to Resolution.IN_FLIGHT: owner_pid mismatches
    this process (forcing Liveness.UNKNOWN, never DEAD) and started_at is
    recent enough that elapsed_s stays under resolve_pending's 120s expiry
    window -- exactly the crashed-owner / still-running-orphaned-helper
    hazard the finding describes.
    """
    import time

    config.save_pending({
        "action": "disable",
        "targets": [{"instance_id": target_id, "friendly_name": friendly_name}],
        "result_file": None,
        "helper_pid": None,
        "helper_pid_start_time": None,
        "owner_pid": 999999,
        "started_at": time.time(),
        "unlocked_at": None,
    })


def _write_dead_helper_record(target_id="DISPLAY\\A\\1", friendly_name="A"):
    """A genuinely UNCONFIRMABLE boot-recovered record (elapsed past
    resolve_pending's 120s expiry window) -- the ordinary case these two
    methods exist to resolve. Mirrors
    test_bridge_revert_now_boot_recovered.py's own fixture.

    `started_at` must be a real timestamp well in the past, not `0.0` --
    `normalize_pending` falls back to the record file's own mtime (~now)
    whenever `started_at` is falsy, so a literal `0.0` would NOT actually
    be stale and would instead resolve to `Resolution.IN_FLIGHT`.
    """
    import time

    config.save_pending({
        "action": "disable",
        "targets": [{"instance_id": target_id, "friendly_name": friendly_name}],
        "result_file": None,
        "helper_pid": None,
        "helper_pid_start_time": None,
        "owner_pid": 999999,
        "started_at": time.time() - 300.0,
        "unlocked_at": None,
    })


class TestRevertNowRefusesWhileInFlight:
    def test_raises_and_never_calls_set_monitors_enabled(self, monkeypatch):
        _write_in_flight_record()
        seen_calls = []
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: seen_calls.append((list(ids), enabled)) or [],
        )
        api = Api()
        assert api._pending_guard is None

        result = api.revert_now()

        assert result["ok"] is False
        # kind="busy" (not "error"): "still in flight" is a genuinely
        # transient, expected rejection -- not a bug in revert_now's own
        # body -- and it leaves self._op_lock/self._boot_armed exactly as
        # recoverable as before the call (see bridge.py's
        # _InFlightStillPending and tests/test_bridge_in_flight_rejection_recoverable.py).
        assert result["kind"] == "busy"
        assert "in flight" in result["message"].lower()
        assert seen_calls == [], (
            "revert_now must never launch a fresh elevated re-enable while "
            "the on-disk record is still IN_FLIGHT -- it could race a still-"
            "running orphaned disable helper on the same device instance"
        )

    def test_record_survives_the_refusal(self, monkeypatch):
        _write_in_flight_record()
        api = Api()

        api.revert_now()

        record = config.load_pending()
        assert record is not None, (
            "refusing must leave the crash-recovery record intact so a "
            "later recheck can still catch the orphaned helper resolving"
        )
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}

    def test_still_works_for_a_genuinely_dead_helper_record(self, monkeypatch):
        # No regression: the normal/expected boot-recovery scenario.
        _write_dead_helper_record()
        seen_calls = []

        def _fake_set_monitors_enabled(ids, enabled, **kwargs):
            seen_calls.append((list(ids), enabled))
            return [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fake_set_monitors_enabled
        )
        api = Api()

        result = api.revert_now()

        assert result["ok"] is True
        assert seen_calls == [(["DISPLAY\\A\\1"], True)]
        assert config.load_pending() is None


class TestKeepDisabledRefusesWhileInFlight:
    def test_raises_and_never_clears_the_record(self, monkeypatch):
        _write_in_flight_record()
        api = Api()
        assert api._pending_guard is None

        result = api.keep_disabled()

        assert result["ok"] is False
        # See the matching kind="busy" note in TestRevertNowRefusesWhileInFlight above.
        assert result["kind"] == "busy"
        assert "in flight" in result["message"].lower()
        record = config.load_pending()
        assert record is not None, (
            "keep_disabled must never discard the crash-recovery record "
            "while the on-disk state is still IN_FLIGHT"
        )
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}

    def test_still_works_for_a_genuinely_dead_helper_record(self, monkeypatch):
        # Round 21 finding 2: keep_disabled()'s guard-is-None branch now
        # trims per-target instead of blanket-clearing the whole on-disk
        # record (see tests/test_bridge_keep_disabled_boot_recovered_scoping.py).
        # A lone UNCONFIRMABLE target's entry is deliberately left in place
        # here -- the record is its only safety net until force_unlock_pending
        # explicitly releases it, matching the same UNCONFIRMABLE-must-survive
        # rule that finding's mixed-batch scenario requires. The result is
        # still a successful "kept" response; only the on-disk record now
        # persists instead of vanishing.
        _write_dead_helper_record()
        api = Api()

        result = api.keep_disabled()

        assert result["ok"] is True
        record = config.load_pending()
        assert record is not None
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}


class TestInFlightGuardOnlyAppliesToTheBootRecoveredPath:
    """A live in-process PendingDisableGuard (guard is not None) can never
    legitimately resolve to IN_FLIGHT for its own targets -- owner_pid on
    disk matches this same process, so process_liveness never reports
    UNKNOWN on that basis. The new guard must therefore be scoped to
    `self._pending_guard is None`, matching the exact hazard the finding
    describes, and must not interfere with the ordinary live-guard
    keep/revert flow even when the on-disk record's own bookkeeping
    (no helper_pid captured, no result file written, a fresh started_at) is
    incidentally IN_FLIGHT-shaped in a mocked test environment.
    """

    def _confirmed_disable(self, monkeypatch):
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

    def test_keep_disabled_with_a_live_guard_is_unaffected(self, monkeypatch):
        api = self._confirmed_disable(monkeypatch)
        assert api._pending_guard is not None

        result = api.keep_disabled()

        assert result["ok"] is True
        assert config.load_pending() is None

    def test_revert_now_with_a_live_guard_is_unaffected(self, monkeypatch):
        api = self._confirmed_disable(monkeypatch)
        assert api._pending_guard is not None
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )

        result = api.revert_now()

        assert result["ok"] is True
        assert config.load_pending() is None

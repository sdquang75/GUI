"""Round 7 finding (Stream 2, HIGH): a second disable of a DIFFERENT monitor
used to silently destroy an earlier, still-open (timed-out, unconfirmed)
crash-recovery record. `_build_and_save_pending_record` wrote
`pending_restore.json` via a full-file atomic overwrite, so monitor B's
batch replaced monitor A's still-unresolved entry outright.

Why round 3's `_check_no_live_guard_conflict` doesn't already catch this:
it only inspects the live `self._pending_guard` object, and a TIMED-OUT
disable never gets a guard armed at all (guards are only armed for
`confirmed_ids` -- round 4 deliberately keeps a timeout's record entry on
disk with no guard, so a future `recover_on_boot` can still surface it).
So a second, unrelated disable sees no armed guard and proceeds straight
into the destructive overwrite.
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


class _FakeTimer:
    """Stand-in for threading.Timer so a confirmed disable's auto-revert
    guard never arms a REAL 10s background daemon thread here.

    Several tests below drive `set_monitors_enabled` to OUTCOME_CONFIRMED,
    which arms the auto-revert guard (`Api._arm_auto_revert_guard` ->
    `_arm_guard_timer` in quickres/webview/bridge.py) and never resolves it
    (no revert_now/keep_disabled call). Without this fixture that would
    construct and start a real `threading.Timer(10.0, ...)` daemon thread
    that outlives this test, fires ~10s later against a monkeypatch already
    torn down by an unrelated test, and can corrupt that other test's own
    timer-count assertions (see round 31 finding on this file).
    """

    instances = []

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.started = False
        self.cancelled = False
        _FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


@pytest.fixture(autouse=True)
def _fake_timer(monkeypatch):
    _FakeTimer.instances = []
    monkeypatch.setattr("quickres.webview.bridge.threading.Timer", _FakeTimer)
    yield _FakeTimer
    _FakeTimer.instances = []


def _two_known_monitors(monkeypatch):
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [
            {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
            {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B", "enabled": True},
        ],
    )


class TestSecondDisableMergesRatherThanDestroys:
    def test_timed_out_disable_then_unrelated_disable_keeps_both_records(self, monkeypatch):
        _two_known_monitors(monkeypatch)
        api = Api()

        # First disable: monitor A times out -- unresolved, no guard armed
        # (round 4 behavior), record must stay on disk.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids
            ],
        )
        result_a = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        assert result_a["ok"] is True
        assert api._pending_guard is None, "a timeout produces no confirmed ids -- no guard armed"

        record_after_a = config.load_pending()
        assert record_after_a is not None
        assert {t["instance_id"] for t in record_after_a["targets"]} == {"DISPLAY\\A\\1"}

        # Second, unrelated disable: monitor B confirms successfully.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        result_b = api.set_monitors_enabled(["DISPLAY\\B\\2"], False)
        assert result_b["ok"] is True

        record_after_b = config.load_pending()
        assert record_after_b is not None
        assert {t["instance_id"] for t in record_after_b["targets"]} == {
            "DISPLAY\\A\\1", "DISPLAY\\B\\2",
        }, "monitor A's still-unresolved entry must survive monitor B's disable, not be destroyed"

    def test_merged_record_preserves_friendly_names_of_both_targets(self, monkeypatch):
        _two_known_monitors(monkeypatch)
        api = Api()
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids
            ],
        )
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        api.set_monitors_enabled(["DISPLAY\\B\\2"], False)

        record = config.load_pending()
        by_id = {t["instance_id"]: t["friendly_name"] for t in record["targets"]}
        assert by_id == {"DISPLAY\\A\\1": "A", "DISPLAY\\B\\2": "B"}

    def test_merge_keeps_the_new_batchs_own_result_file_and_owner_pid_live(self, monkeypatch):
        # The merged record's action-in-flight fields (result_file, helper_pid,
        # owner_pid, started_at) describe the batch actually launching now --
        # its own helper is what _save_helper_pid/_resolve_pending_now track --
        # while only `targets` is unioned with whatever was already pending.
        _two_known_monitors(monkeypatch)
        api = Api()
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids
            ],
        )
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        record_a = config.load_pending()

        captured = {}

        def _fake_disable(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
            captured["result_path"] = result_path
            if on_helper_launched:
                on_helper_launched(4242)
            return [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fake_disable
        )
        api.set_monitors_enabled(["DISPLAY\\B\\2"], False)

        record_b = config.load_pending()
        assert record_b["result_file"] == captured["result_path"]
        assert record_b["result_file"] != record_a["result_file"]
        assert record_b["helper_pid"] == 4242

    def test_no_existing_pending_record_still_works_as_before(self, monkeypatch):
        _two_known_monitors(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        record = config.load_pending()
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}


class TestGuardConflictCheckRemainsScopedToTheLiveGuard:
    """Decision (round 7): `_check_no_live_guard_conflict` stays scoped to
    `self._pending_guard` -- it is not widened to refuse a new disable just
    because an unrelated, already-timed-out target still sits on disk.

    Rationale: rounds 5/6 established that multiple unresolved targets
    legitimately coexisting in the SAME on-disk record is normal (a 3-monitor
    batch where some confirm and one times out), tracked passively for
    `recover_on_boot` to surface later -- not something that blocks other
    operations. A stale timeout can persist indefinitely (the helper may
    never report back), so refusing every future disable until it resolves
    would let one flaky elevation prompt permanently block the user from
    disabling any other monitor. The guard-armed check's original purpose
    (round 3) was narrowly to protect the short, deterministic 10s
    auto-revert grace window from a racing second disable -- that scope is
    preserved unchanged here.
    """

    def test_unrelated_timed_out_target_does_not_block_a_new_disable(self, monkeypatch):
        _two_known_monitors(monkeypatch)
        api = Api()
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids
            ],
        )
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        result = api.set_monitors_enabled(["DISPLAY\\B\\2"], False)

        assert result["ok"] is True, "an unrelated timed-out target must not refuse a new disable"

    def test_live_armed_guard_still_refuses_a_second_disable(self, monkeypatch):
        # Unchanged pre-existing behavior: a CONFIRMED disable arms a real
        # guard, and that guard still refuses an overlapping/second disable
        # until resolved.
        _two_known_monitors(monkeypatch)
        api = Api()
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        assert api._pending_guard is not None

        result = api.set_monitors_enabled(["DISPLAY\\B\\2"], False)

        assert result["ok"] is False
        assert result["kind"] == "error"

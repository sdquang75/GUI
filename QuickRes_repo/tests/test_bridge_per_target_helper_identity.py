"""Round 24 finding (R3 Reliability, HIGH): a crash-recovery record's helper
identity (helper_pid/owner_pid/helper_pid_start_time) used to be tracked as a
single record-GLOBAL triple even though a record's "targets" list can hold
multiple independently-originated batches merged together
(`_build_and_save_pending_record`'s union). Whichever batch's helper launched
*last* blindly overwrote that global triple for every target in the record --
including targets that belong to an entirely different, still-possibly-alive
helper.

Failure chain proven here: disable monitor A -> times out (helper H1), no
guard armed, its entry stays on disk. Disable monitor B shortly after ->
`_build_and_save_pending_record` unions targets into [A, B] and
`_save_helper_pid` used to overwrite the record's single global helper_pid to
B's helper H2. B confirms and H2 exits normally; once B's targets are trimmed
out of the record (`_remove_targets_from_pending`), only A's entry remains --
but the record-level helper_pid still says H2 (dead). Retrying A must read
A's OWN helper identity (H1), not B's now-dead H2, or `_check_no_stale_record_
conflict` falsely reports DEAD and lets a second concurrent elevated helper
race the first one against the same device.

Fix: each entry in the on-disk record's "targets" list now carries its own
helper_pid/owner_pid/helper_pid_start_time, populated by whichever batch most
recently launched a helper for THAT specific target. The record-level fields
of the same names are kept (existing callers/tests still read them, and
`_resolve_pending_now`'s liveness sampling is untouched by this fix), but
`_check_no_stale_record_conflict` now reads the per-target fields as the
source of truth, falling back to the record-level fields only for an
old-schema on-disk record written before this fix landed (detected by the
per-target "helper_pid" key being entirely absent).
"""
import os
import threading
import time

import pytest

from quickres.webview.bridge import Api
from quickres import config, monitors, recovery


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


class _FakeTimer:
    """Stand-in for `threading.Timer` used by every test in this file.

    Round-33 finding (HIGH): a CONFIRMED `Api.set_monitors_enabled(..., False)`
    call drives `_arm_auto_revert_guard` -> `_arm_guard_timer`, which -- unless
    `quickres.webview.bridge.threading.Timer` is mocked -- constructs a REAL
    daemon `threading.Timer(10.0, ...)` and starts it. No test in this file
    ever resolves the resulting guard before returning, so that real timer
    (and, since `bridge.py` does a plain `import threading`, any bounded
    retry timer re-armed on top of it) used to keep running as a live
    background thread past this test's own teardown -- firing ~10s later,
    reaching the real (forbidden-by-conftest) elevation seam, and, because
    the monkeypatch of `threading.Timer` is process-wide rather than scoped
    to this module, potentially landing in an unrelated test's own
    `_FakeTimer.instances` list. Faking the timer here (the same pattern
    already used by tests/test_bridge_auto_revert_retry.py and
    tests/test_bridge_revert_now_failure_retry.py) means `_arm_guard_timer`
    never touches the real `threading` module at all, so no background
    thread is ever created and there is nothing left to leak.
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


def _routed_liveness(routing):
    def _fn(helper_pid, owner_pid, helper_pid_start_time=None):
        return routing.get(helper_pid, recovery.Liveness.UNKNOWN)
    return _fn


def _disable_a_times_out(monkeypatch, helper_pid=1111):
    def _timeout_with_pid(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
        if on_helper_launched:
            on_helper_launched(helper_pid)
        return [(iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_AMBIGUOUS) for iid in ids]
    monkeypatch.setattr("quickres.webview.bridge.monitors.set_monitors_enabled", _timeout_with_pid)


def _disable_b_confirms(monkeypatch, helper_pid=2222):
    def _confirm_with_pid(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
        if on_helper_launched:
            on_helper_launched(helper_pid)
        return [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids]
    monkeypatch.setattr("quickres.webview.bridge.monitors.set_monitors_enabled", _confirm_with_pid)


class TestPerTargetHelperIdentitySurvivesSiblingTrim:
    def test_confirmed_disable_does_not_leak_a_real_background_timer_thread(self, monkeypatch):
        _two_known_monitors(monkeypatch)
        api = Api()

        threads_before = set(threading.enumerate())

        _disable_b_confirms(monkeypatch, helper_pid=2222)
        api.set_monitors_enabled(["DISPLAY\\B\\2"], False)

        # A CONFIRMED outcome arms an auto-revert guard timer
        # (`_arm_auto_revert_guard` -> `_arm_guard_timer`). It must be the
        # module-level `_FakeTimer` fixture, not a real `threading.Timer`,
        # so no live daemon thread is left running once this test returns
        # -- regardless of whether the guard is ever resolved.
        assert len(_FakeTimer.instances) == 1
        assert _FakeTimer.instances[0].started is True
        new_threads = threading.enumerate() and set(threading.enumerate()) - threads_before
        assert new_threads == set(), (
            "set_monitors_enabled(..., False) with a CONFIRMED outcome must "
            "not start any real background thread; quickres.webview.bridge."
            "threading.Timer must be mocked for the entire duration of this "
            "test file"
        )

    def test_merged_batch_writes_per_target_helper_identity_not_just_global(self, monkeypatch):
        _two_known_monitors(monkeypatch)
        api = Api()

        _disable_a_times_out(monkeypatch, helper_pid=1111)
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        _disable_b_confirms(monkeypatch, helper_pid=2222)
        api.set_monitors_enabled(["DISPLAY\\B\\2"], False)

        record = config.load_pending()
        # Record-level fields still describe the most-recently-launched
        # batch (existing/kept behavior, still relied on elsewhere).
        assert record["helper_pid"] == 2222
        by_id = {t["instance_id"]: t for t in record["targets"]}
        assert by_id["DISPLAY\\A\\1"]["helper_pid"] == 1111, (
            "A's own per-target helper identity must not be overwritten by "
            "B's later batch write"
        )
        assert by_id["DISPLAY\\B\\2"]["helper_pid"] == 2222

    def test_retry_of_trimmed_siblings_target_reads_its_own_still_alive_helper(self, monkeypatch):
        _two_known_monitors(monkeypatch)
        api = Api()

        _disable_a_times_out(monkeypatch, helper_pid=1111)
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        _disable_b_confirms(monkeypatch, helper_pid=2222)
        api.set_monitors_enabled(["DISPLAY\\B\\2"], False)

        # B's auto-revert guard resolves (here: the user manually re-enables
        # B, which resolves its guard the same way an expired auto-revert
        # would) and its target is trimmed out of the on-disk record -- only
        # A's entry remains. The record-level helper_pid still says H2 (now
        # dead), a stale value that must no longer be consulted for A's own
        # liveness.
        api._resolve_guard_for_enabled_ids(["DISPLAY\\B\\2"])
        record = config.load_pending()
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}
        assert record["helper_pid"] == 2222

        # A's real helper (H1=1111) is still alive; B's stale helper
        # (H2=2222) is dead. A correct per-target check must block the
        # retry because A's OWN helper is still alive -- a check that
        # (incorrectly) reads the stale record-level field would see H2
        # (dead) and wrongly allow it.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness",
            _routed_liveness({1111: recovery.Liveness.ALIVE, 2222: recovery.Liveness.DEAD}),
        )
        launched = {"called": False}

        def _fail_if_launched(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
            launched["called"] = True
            return [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fail_if_launched
        )

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is False, (
            "must read A's OWN still-alive helper identity, not B's stale "
            "dead one, when deciding whether to refuse the retry"
        )
        assert launched["called"] is False, (
            "must not launch a second concurrent helper while A's own "
            "helper's liveness is not confirmed dead"
        )

    def test_retry_of_trimmed_siblings_target_proceeds_once_its_own_helper_is_dead(self, monkeypatch):
        _two_known_monitors(monkeypatch)
        api = Api()

        _disable_a_times_out(monkeypatch, helper_pid=1111)
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        _disable_b_confirms(monkeypatch, helper_pid=2222)
        api.set_monitors_enabled(["DISPLAY\\B\\2"], False)

        api._resolve_guard_for_enabled_ids(["DISPLAY\\B\\2"])

        # This time A's own helper (H1=1111) is genuinely confirmed dead --
        # the retry must be allowed to proceed even though B's stale
        # record-level helper (H2=2222) is routed as ALIVE here, proving the
        # check is genuinely reading A's own identity, not just always
        # blocking.
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness",
            _routed_liveness({1111: recovery.Liveness.DEAD, 2222: recovery.Liveness.ALIVE}),
        )
        launched = {"called": False}

        def _fresh_helper(ids, enabled, result_path=None, on_helper_launched=None, **kwargs):
            launched["called"] = True
            if on_helper_launched:
                on_helper_launched(3333)
            return [(iid, True, "Disabled", monitors.OUTCOME_CONFIRMED) for iid in ids]

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled", _fresh_helper
        )

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        assert launched["called"] is True


class TestBackwardCompatibilityWithPreFixRecordShape:
    def test_falls_back_to_record_level_fields_when_target_carries_no_own_identity(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors",
            lambda: [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True}],
        )
        # An on-disk record written before this fix landed: targets carry no
        # per-target helper_pid/owner_pid/helper_pid_start_time keys at all.
        config.save_pending({
            "action": "disable",
            "targets": [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A"}],
            "result_file": None,
            "helper_pid": 9999,
            "helper_pid_start_time": None,
            "owner_pid": os.getpid(),
            "started_at": time.time(),
            "unlocked_at": None,
        })
        api = Api()

        captured = {}

        def _capture(helper_pid, owner_pid, helper_pid_start_time=None):
            captured["helper_pid"] = helper_pid
            return recovery.Liveness.ALIVE

        monkeypatch.setattr("quickres.webview.bridge.monitors.process_liveness", _capture)

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is False
        assert captured["helper_pid"] == 9999, (
            "an old-schema record with no per-target helper identity must "
            "fall back to the record-level helper_pid"
        )

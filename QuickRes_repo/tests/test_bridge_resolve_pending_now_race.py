"""Round 9 finding (MEDIUM): `_resolve_pending_now` (reused by
`recover_on_boot`, itself reached from the unlocked `get_initial_state`)
had no lock protection. Two concurrent calls -- a double-click of the round
5 boot-error Retry button, or `get_initial_state` racing `recheck_pending`
-- can race each other over the same on-disk `pending_restore.json` record
AND the same helper result file, which `monitors.read_op_result` DELETES on
read: a second concurrent caller reading right after the first one's delete
gets nothing, corrupting the crash-recovery resolution for that boot/check
cycle.

`_resolve_pending_now_bounded_under_lock` closes this for `recover_on_boot`'s call
site with a SHORT bounded blocking acquire of `self._op_lock` (not
unbounded -- `get_initial_state` is user-re-invocable, so waiting forever
on a lock only released by the user acting on the recovery UI this very
call renders would freeze the app instead of protecting it).
"""
import json
import os
import threading
import time

import pytest

from quickres.webview.bridge import Api
from quickres import config
from quickres import recovery


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


class TestConcurrentCallsAreSerializedNotRacing:
    def test_two_concurrent_calls_never_execute_the_raw_resolution_at_once(self, monkeypatch):
        api = Api()
        in_flight = {"count": 0, "max_seen": 0}
        guard_lock = threading.Lock()

        def _slow_raw_resolve():
            with guard_lock:
                in_flight["count"] += 1
                in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["count"])
            time.sleep(0.15)
            with guard_lock:
                in_flight["count"] -= 1
            return []

        monkeypatch.setattr(api, "_resolve_pending_now", _slow_raw_resolve)

        results = []

        def _call():
            results.append(api._resolve_pending_now_bounded_under_lock())

        t1 = threading.Thread(target=_call)
        t2 = threading.Thread(target=_call)
        t1.start()
        time.sleep(0.03)  # Ensure t1 has already acquired the lock.
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not t1.is_alive()
        assert not t2.is_alive()
        assert in_flight["max_seen"] == 1, (
            "two concurrent callers ran the raw resolution simultaneously -- "
            "they raced instead of being serialized by self._op_lock"
        )
        assert results == [[], []]

    def test_second_reader_does_not_see_a_result_file_already_deleted_by_the_first(
        self, monkeypatch
    ):
        # A more end-to-end version of the same race: the first caller's
        # (real) read_op_result deletes the on-disk result file; a second,
        # truly concurrent caller must be blocked out until the first one
        # finishes, rather than independently racing into a second
        # (corrupted, already-deleted) read of the same file.
        api = Api()
        config.save_pending({
            "action": "disable",
            "targets": [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A"}],
            "result_file": None,
            "helper_pid": None,
            "owner_pid": 999999,
            "started_at": time.time(),
            "unlocked_at": None,
        })

        entered_first_read = threading.Event()
        release_first_read = threading.Event()
        read_calls = []

        def _paused_normalize(raw, mtime):
            # normalize_pending is called once per _resolve_pending_now
            # invocation, right at the top -- use it as the pause point so
            # we can prove the SECOND call never even starts its own body
            # until the first one has released the lock.
            read_calls.append(1)
            if len(read_calls) == 1:
                entered_first_read.set()
                release_first_read.wait(timeout=2)
            return {
                "action": "disable",
                "targets": [{"instance_id": "DISPLAY\\A\\1", "friendly_name": "A"}],
                "result_file": None,
                "helper_pid": None,
                "owner_pid": 999999,
                "started_at": time.time(),
                "unlocked_at": None,
            }

        monkeypatch.setattr(
            "quickres.webview.bridge.recovery.normalize_pending", _paused_normalize
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness",
            lambda *a, **k: __import__("quickres.recovery", fromlist=["Liveness"]).Liveness.DEAD,
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.sample_device_states", lambda ids: {}
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.recovery.resolve_pending",
            lambda *a, **k: [],
        )

        first_results = []
        second_results = []

        t1 = threading.Thread(
            target=lambda: first_results.append(api._resolve_pending_now_bounded_under_lock())
        )
        t1.start()
        assert entered_first_read.wait(timeout=2), "first call never reached normalize_pending"

        t2 = threading.Thread(
            target=lambda: second_results.append(api._resolve_pending_now_bounded_under_lock())
        )
        t2.start()
        # Give t2 a moment to attempt (and, if the bug were present, wrongly
        # succeed at) its own concurrent entry into the critical section.
        time.sleep(0.1)
        assert len(read_calls) == 1, (
            "a second concurrent caller entered the raw resolution while "
            "the first was still mid-flight -- self._op_lock did not "
            "serialize them"
        )

        release_first_read.set()
        t1.join(timeout=2)
        t2.join(timeout=2)

        assert not t1.is_alive()
        assert not t2.is_alive()
        assert len(read_calls) == 2, "second caller should proceed once the lock frees"


class TestBoundedTimeoutFallsBackWithoutTouchingDisk:
    def test_timeout_returns_empty_outcomes_without_reading_pending(self, monkeypatch):
        api = Api()
        monkeypatch.setattr(
            "quickres.webview.bridge._RESOLVE_PENDING_LOCK_TIMEOUT_S", 0.05
        )
        api._op_lock.acquire()  # simulate another operation genuinely holding it
        touched = {"called": False}

        def _fail_if_called():
            touched["called"] = True
            return []

        monkeypatch.setattr(api, "_resolve_pending_now", _fail_if_called)

        outcomes = api._resolve_pending_now_bounded_under_lock()

        assert outcomes is None, (
            "a bounded-lock timeout must be distinguishable (None) from a "
            "genuine empty resolution ([]) -- recover_on_boot relies on "
            "this to know a timeout must never be cached as a completed "
            "resolution"
        )
        assert touched["called"] is False, (
            "must not read/mutate the on-disk record when it could not "
            "acquire the lock within the bounded timeout"
        )
        api._op_lock.release()


class TestRecoverOnBootUsesTheLockedResolution(object):
    def test_recover_on_boot_calls_the_locked_wrapper_not_the_raw_method(self, monkeypatch):
        api = Api()
        calls = {"locked": 0, "raw": 0}

        def _fake_locked():
            calls["locked"] += 1
            return []

        def _fake_raw():
            calls["raw"] += 1
            return []

        monkeypatch.setattr(api, "_resolve_pending_now_bounded_under_lock", _fake_locked)
        monkeypatch.setattr(api, "_resolve_pending_now", _fake_raw)

        result = api.recover_on_boot()

        assert calls["locked"] == 1
        assert calls["raw"] == 0
        assert result == {"outcomes": [], "force_unlockable": False}


class TestRecoverOnBootIdempotentAcrossRepeatedCalls:
    """Finding (round 18, HIGH): `_resolve_pending_now_bounded_under_lock`'s
    lock only serializes ACCESS to the on-disk record/result file -- it does
    NOT make two calls to `recover_on_boot()` that run one strictly after
    the other (not concurrently) compute the SAME resolution.
    `monitors.read_op_result` destructively deletes the helper result file
    on its first read, so a second, later `recover_on_boot()` call that
    still finds the pending record on disk (because the first call's mixed
    outcome set left it there for a manual force-unlock) re-derives its
    resolution from a now-emptied result file and can land on a WORSE/
    DIFFERENT outcome for a target the first call already resolved
    confidently.

    This test exercises the REAL destructive read (no monkeypatching of
    `monitors.read_op_result` or `recovery.resolve_pending`) and proves a
    second call's resolution is IDENTICAL to the first call's, not merely
    that the two calls do not overlap in time.
    """

    def test_second_call_after_a_completed_first_call_returns_the_identical_resolution(
        self, monkeypatch
    ):
        old_started_at = time.time() - 500  # past the 120s expiry window
        result_path = os.path.join(config.APP_DIR, "monitor_op_result_111_222.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(
                {"results": [{"instance_id": "DISPLAY\\A\\1", "ok": True, "message": ""}]},
                f,
            )
        config.save_pending({
            "action": "disable",
            "targets": [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A"},
                {"instance_id": "DISPLAY\\B\\1", "friendly_name": "B"},
            ],
            "result_file": result_path,
            "helper_pid": 999,
            "owner_pid": 111,
            "started_at": old_started_at,
            "unlocked_at": None,
        })
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.process_liveness",
            lambda helper_pid, owner_pid, **kwargs: recovery.Liveness.DEAD,
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.sample_device_states",
            lambda ids: {iid: None for iid in ids},
        )

        api = Api()

        first = api.recover_on_boot()
        # Sanity checks on the setup itself, so a future change to this
        # test's fixtures fails loudly here instead of silently no-op'ing
        # the real regression check below.
        assert not os.path.exists(result_path), (
            "setup assumption broken: the real read_op_result must have "
            "consumed (deleted) the result file on the first call"
        )
        assert config.load_pending() is not None, (
            "setup assumption broken: the mixed outcome set must leave the "
            "on-disk record in place after the first call"
        )
        first_by_id = {o["instance_id"]: o["resolution"] for o in first["outcomes"]}
        assert first_by_id["DISPLAY\\A\\1"] == "disabled_confirmed"
        assert first_by_id["DISPLAY\\B\\1"] == "unconfirmable"
        assert first["force_unlockable"] is True

        second = api.recover_on_boot()

        assert second == first, (
            "a second recover_on_boot() call diverged from the first, "
            "already-completed call's resolution -- it must return the "
            "first call's cached resolution instead of re-deriving one "
            "from the destructively-read (now-empty) result file"
        )

import pytest

from quickres import monitors
from quickres.monitors import PendingDisableGuard


def test_timeout_s_property_exposes_constructor_value():
    # 1g: bridge.py's real threading.Timer wiring needs to read the guard's
    # own configured timeout instead of a second hardcoded literal.
    guard = PendingDisableGuard(
        armed_at=0.0, target_ids=["A"], revert_fn=lambda ids: None, timeout_s=10.0
    )

    assert guard.timeout_s == 10.0


def test_remaining_s_reflects_true_elapsed_time_on_reopen():
    # MON-5 scenario: armed 4 seconds ago -> ~6s remaining, not a fresh 10.
    guard = PendingDisableGuard(armed_at=0.0, target_ids=["A"], revert_fn=lambda ids: None)

    assert guard.remaining_s(now=4.0) == 6.0


def test_is_expired_boundary_at_exactly_10s_true():
    guard = PendingDisableGuard(armed_at=0.0, target_ids=["A"], revert_fn=lambda ids: None)

    assert guard.is_expired(now=10.0) is True


def test_is_expired_boundary_just_under_10s_false():
    guard = PendingDisableGuard(armed_at=0.0, target_ids=["A"], revert_fn=lambda ids: None)

    assert guard.is_expired(now=9.9) is False


def _ok_revert(calls):
    # Matches the real production revert_fn's return shape (mirrors
    # monitors.set_monitors_enabled): list[(instance_id, ok, message, kind)].
    def _revert(ids):
        calls.append(ids)
        return [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids]
    return _revert


def test_expiry_triggers_single_revert_call_covering_all_targets():
    calls = []
    guard = PendingDisableGuard(
        armed_at=0.0, target_ids=["A", "B", "C"], revert_fn=_ok_revert(calls)
    )

    triggered = guard.check(now=10.0)

    assert triggered is True
    assert calls == [["A", "B", "C"]]  # one call, all targets -- not one per target


def test_check_before_expiry_does_not_revert():
    calls = []
    guard = PendingDisableGuard(
        armed_at=0.0, target_ids=["A"], revert_fn=_ok_revert(calls)
    )

    triggered = guard.check(now=5.0)

    assert triggered is False
    assert calls == []


def test_confirm_before_expiry_prevents_later_revert():
    calls = []
    guard = PendingDisableGuard(
        armed_at=0.0, target_ids=["A"], revert_fn=_ok_revert(calls)
    )

    guard.confirm()
    triggered = guard.check(now=15.0)

    assert triggered is False
    assert calls == []


def test_check_does_not_revert_twice_on_repeated_polls_past_expiry():
    calls = []
    guard = PendingDisableGuard(
        armed_at=0.0, target_ids=["A"], revert_fn=_ok_revert(calls)
    )

    guard.check(now=10.0)
    guard.check(now=11.0)

    assert calls == [["A"]]


def test_check_leaves_guard_unresolved_when_revert_fn_raises():
    # Round-3 finding 4: check() used to mark itself resolved BEFORE calling
    # revert_fn -- if revert_fn (a real Win32/ctypes call chain in
    # production) raises, the guard was already considered "resolved" even
    # though the actual revert never completed, so the caller's
    # crash-recovery record could be cleared/lost for a device that's still
    # genuinely disabled. resolved must only flip to True once revert_fn has
    # actually succeeded.
    def _boom(ids):
        raise RuntimeError("simulated Win32 failure")

    guard = PendingDisableGuard(armed_at=0.0, target_ids=["A"], revert_fn=_boom)

    with pytest.raises(RuntimeError):
        guard.check(now=10.0)

    assert guard.resolved is False


def test_check_retries_revert_on_next_poll_after_a_prior_raise():
    calls = []

    def _fail_once(ids):
        calls.append(ids)
        if len(calls) == 1:
            raise RuntimeError("simulated Win32 failure")
        return [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids]

    guard = PendingDisableGuard(armed_at=0.0, target_ids=["A"], revert_fn=_fail_once)

    with pytest.raises(RuntimeError):
        guard.check(now=10.0)
    triggered = guard.check(now=11.0)

    assert triggered is True
    assert guard.resolved is True
    assert calls == [["A"], ["A"]]


class TestCheckTrustsPerIdResultsNotJustNoException:
    """Round 6 CRITICAL finding: the real production revert_fn (bridge.py's
    lambda around monitors.set_monitors_enabled) never raises for an
    expected failure -- it always returns per-id (id, False, message)
    tuples instead. check() must inspect that return value and only treat
    ok=True results as genuinely resolved, exposing the full per-id split
    via last_results so callers can make correct trim/clear decisions.
    """

    def test_all_targets_failing_without_raising_leaves_guard_unresolved(self):
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A", "B"],
            revert_fn=lambda ids: [
                (iid, False, "Elevation was cancelled", monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ],
        )

        triggered = guard.check(now=10.0)

        assert triggered is True, "an attempt was made -- this is not the same as success"
        assert guard.resolved is False, (
            "every target failed -- 'revert_fn did not raise' must never be "
            "trusted as 'genuinely resolved'"
        )
        assert guard.last_results == [
            ("A", False, "Elevation was cancelled", monitors.OUTCOME_GENUINE_FAILURE),
            ("B", False, "Elevation was cancelled", monitors.OUTCOME_GENUINE_FAILURE),
        ]

    def test_partial_failure_leaves_guard_unresolved(self):
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A", "B"],
            revert_fn=lambda ids: [
                ("A", True, "ok", monitors.OUTCOME_CONFIRMED),
                ("B", False, "still disabled", monitors.OUTCOME_GENUINE_FAILURE),
            ],
        )

        triggered = guard.check(now=10.0)

        assert triggered is True
        assert guard.resolved is False, (
            "B genuinely failed -- the guard as a whole must not be marked "
            "resolved just because A succeeded"
        )
        assert guard.last_results == [
            ("A", True, "ok", monitors.OUTCOME_CONFIRMED),
            ("B", False, "still disabled", monitors.OUTCOME_GENUINE_FAILURE),
        ]

    def test_full_success_resolves_and_exposes_results(self):
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A", "B"],
            revert_fn=lambda ids: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )

        triggered = guard.check(now=10.0)

        assert triggered is True
        assert guard.resolved is True
        assert guard.last_results == [
            ("A", True, "ok", monitors.OUTCOME_CONFIRMED),
            ("B", True, "ok", monitors.OUTCOME_CONFIRMED),
        ]

    def test_last_results_is_none_before_any_check_fires(self):
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A"],
            revert_fn=lambda ids: [(iid, True, "ok", monitors.OUTCOME_CONFIRMED) for iid in ids],
        )

        assert guard.last_results is None


class TestRemoveTargets:
    """Round 11 HIGH finding: manually enabling ONE monitor out of a
    multi-target guard used to cancel the WHOLE guard (bridge.py's
    _resolve_guard_for_enabled_ids), destroying auto-revert protection for
    every OTHER target in the same batch even though they stayed disabled.
    remove_targets() gives the guard real partial-target resolution: a
    caller can drop specific ids from the tracked set without firing
    revert_fn or fully resolving the guard, leaving it armed for whatever
    targets remain.
    """

    def test_removing_a_strict_subset_keeps_guard_unresolved(self):
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A", "B", "C"], revert_fn=lambda ids: None,
        )

        guard.remove_targets(["A"])

        assert guard.resolved is False
        assert guard.target_ids == ["B", "C"]

    def test_removing_every_remaining_target_resolves_the_guard(self):
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A", "B"], revert_fn=lambda ids: None,
        )

        guard.remove_targets(["A", "B"])

        assert guard.resolved is True
        assert guard.target_ids == []

    def test_removing_targets_incrementally_resolves_only_once_empty(self):
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A", "B"], revert_fn=lambda ids: None,
        )

        guard.remove_targets(["A"])
        assert guard.resolved is False

        guard.remove_targets(["B"])
        assert guard.resolved is True

    def test_removed_targets_are_never_reverted_by_a_later_expiry(self):
        calls = []
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A", "B"], revert_fn=_ok_revert(calls),
        )

        guard.remove_targets(["A"])
        triggered = guard.check(now=10.0)

        assert triggered is True
        assert calls == [["B"]], "a removed target must never be included in a later revert call"
        assert guard.resolved is True

    def test_remove_targets_is_a_noop_once_already_resolved(self):
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A"], revert_fn=lambda ids: None,
        )
        guard.confirm()

        guard.remove_targets(["A"])

        assert guard.resolved is True
        assert guard.target_ids == ["A"], (
            "once resolved, the tracked target list is frozen -- a stray "
            "late removal call must not mutate it"
        )

    def test_remove_targets_ignores_unknown_ids(self):
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A", "B"], revert_fn=lambda ids: None,
        )

        guard.remove_targets(["Z"])

        assert guard.resolved is False
        assert guard.target_ids == ["A", "B"]


class TestRearm:
    """Round 32 CONFIRMED finding: is_expired()/check() used to key off the
    guard's ORIGINAL armed_at/timeout_s (set once at __init__) forever --
    once a guard passed its first deadline, is_expired stayed permanently
    True for the rest of its life no matter how far in the future the next
    scheduled backoff retry actually was. bridge.py's _arm_guard_timer is
    the single primitive behind both the initial grace period and every
    bounded auto-revert retry re-arm, but it only started a new
    threading.Timer -- it never updated the guard's own schedule. Any of
    the three call sites that can resolve a guard outside its own timer
    (the scheduled timer itself, webview/app.py's window-close handler, and
    confirm_update -- both of the latter call
    _resolve_guard_unbounded_under_lock with source_timer=None, which skips
    the stale-timer guard entirely) could therefore trigger an
    out-of-schedule live elevation attempt as soon as the guard had expired
    ONCE, not only when ITS OWN next scheduled attempt actually came due.
    rearm(now, delay_s) gives the guard a real "time until the next
    legitimate attempt is due" concept that a re-arming caller (bridge.py's
    _arm_guard_timer) can update in lockstep with the real timer it starts.
    """

    def test_rearm_pushes_the_expiry_deadline_out_to_the_new_schedule(self):
        # Armed at t=0 with a 10s window; first deadline at t=10. A caller
        # re-arming a 5s backoff retry at t=10 must push the NEXT deadline
        # out to t=15, not leave is_expired permanently True from t=10 on.
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A"], revert_fn=lambda ids: None, timeout_s=10.0
        )
        assert guard.is_expired(now=10.0) is True  # first scheduled deadline

        guard.rearm(now=10.0, delay_s=5.0)

        assert guard.is_expired(now=12.0) is False, (
            "a caller resolving the guard between two scheduled attempts "
            "(window-close/confirm_update, both source_timer=None) must "
            "not see it as expired ahead of the real next retry"
        )
        assert guard.remaining_s(now=12.0) == 3.0
        assert guard.is_expired(now=15.0) is True  # the real next deadline

    def test_rearm_updates_the_timeout_s_property(self):
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A"], revert_fn=lambda ids: None, timeout_s=10.0
        )

        guard.rearm(now=10.0, delay_s=5.0)

        assert guard.timeout_s == 5.0

    def test_rearm_is_a_noop_once_the_guard_is_already_resolved(self):
        # A stray late rearm (e.g. a retry scheduled the same instant
        # check() itself fully resolves the guard) must not reopen or
        # reschedule an already-frozen guard.
        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A"], revert_fn=lambda ids: None, timeout_s=10.0
        )
        guard.confirm()

        guard.rearm(now=10.0, delay_s=5.0)

        assert guard.timeout_s == 10.0

    def test_reproduces_the_round_32_scenario_end_to_end(self):
        # armed t=0/timeout=10s; first scheduled check at t=10 partially
        # fails (one real revert attempt, guard stays unresolved) and a
        # retry is re-armed for t=15 via rearm(now=10, delay_s=5). A
        # window-close/confirm_update style caller resolving the guard at
        # t=12 -- ahead of the real t=15 retry -- must NOT fire a second
        # live revert attempt.
        calls = []

        def _always_fail(ids):
            calls.append(ids)
            return [(iid, False, "still stuck", monitors.OUTCOME_GENUINE_FAILURE) for iid in ids]

        guard = PendingDisableGuard(
            armed_at=0.0, target_ids=["A"], revert_fn=_always_fail, timeout_s=10.0
        )

        triggered_at_10 = guard.check(now=10.0)
        assert triggered_at_10 is True
        assert calls == [["A"]]

        guard.rearm(now=10.0, delay_s=5.0)  # bridge.py's backoff re-arm

        triggered_at_12 = guard.check(now=12.0)
        assert triggered_at_12 is False, (
            "a call at t=12, ahead of the real t=15 retry, must not fire a "
            "second out-of-schedule live revert attempt"
        )
        assert calls == [["A"]], "still exactly one revert attempt so far"

        triggered_at_15 = guard.check(now=15.0)
        assert triggered_at_15 is True  # the real, scheduled second attempt
        assert calls == [["A"], ["A"]]

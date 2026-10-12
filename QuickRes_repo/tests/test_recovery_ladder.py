import inspect

import pytest

from quickres.recovery import Liveness, Resolution, resolve_pending


def _record(n_targets=1, started_at=1000.0, unlocked_at=None):
    return {
        "action": "disable",
        "targets": [
            {"instance_id": f"id-{i}", "friendly_name": f"Monitor {i}"} for i in range(n_targets)
        ],
        "owner_pid": 111,
        "started_at": started_at,
        "unlocked_at": unlocked_at,
    }


def _ids(n_targets):
    return [f"id-{i}" for i in range(n_targets)]


@pytest.mark.parametrize("n_targets", [1, 3])
def test_unlocked_at_short_circuits_to_unlocked_unconfirmed(n_targets):
    record = _record(n_targets, unlocked_at=2000.0)
    outcomes = resolve_pending(
        record,
        now=2100.0,
        liveness=Liveness.DEAD,
        helper_results={},
        device_states={},
    )
    assert len(outcomes) == n_targets
    for outcome in outcomes:
        assert outcome.resolution == Resolution.UNLOCKED_UNCONFIRMED
        assert outcome.can_force_unlock is False


@pytest.mark.parametrize("n_targets", [1, 3])
def test_unlocked_at_takes_priority_over_helper_results(n_targets):
    record = _record(n_targets, unlocked_at=2000.0)
    helper_results = {iid: (True, "Disabled") for iid in _ids(n_targets)}
    outcomes = resolve_pending(
        record,
        now=2100.0,
        liveness=Liveness.ALIVE,
        helper_results=helper_results,
        device_states={iid: False for iid in _ids(n_targets)},
    )
    for outcome in outcomes:
        assert outcome.resolution == Resolution.UNLOCKED_UNCONFIRMED


@pytest.mark.parametrize("n_targets", [1, 3])
def test_helper_result_ok_true_resolves_disabled_confirmed(n_targets):
    record = _record(n_targets)
    helper_results = {iid: (True, "Disabled") for iid in _ids(n_targets)}
    outcomes = resolve_pending(
        record,
        now=1010.0,
        liveness=Liveness.DEAD,
        helper_results=helper_results,
        device_states={},
    )
    assert len(outcomes) == n_targets
    for outcome in outcomes:
        assert outcome.resolution == Resolution.DISABLED_CONFIRMED
        assert outcome.can_force_unlock is False
        assert outcome.message


@pytest.mark.parametrize("n_targets", [1, 3])
def test_helper_result_ok_false_resolves_failed(n_targets):
    record = _record(n_targets)
    helper_results = {iid: (False, "Access denied") for iid in _ids(n_targets)}
    outcomes = resolve_pending(
        record,
        now=1010.0,
        liveness=Liveness.DEAD,
        helper_results=helper_results,
        device_states={},
    )
    for outcome in outcomes:
        assert outcome.resolution == Resolution.FAILED
        assert outcome.can_force_unlock is False


@pytest.mark.parametrize("n_targets", [1, 3])
def test_device_state_false_resolves_disabled_confirmed(n_targets):
    record = _record(n_targets)
    outcomes = resolve_pending(
        record,
        now=1010.0,
        liveness=Liveness.DEAD,
        helper_results={},
        device_states={iid: False for iid in _ids(n_targets)},
    )
    for outcome in outcomes:
        assert outcome.resolution == Resolution.DISABLED_CONFIRMED


@pytest.mark.parametrize("n_targets", [1, 3])
def test_device_state_true_resolves_clear(n_targets):
    record = _record(n_targets)
    outcomes = resolve_pending(
        record,
        now=1010.0,
        liveness=Liveness.DEAD,
        helper_results={},
        device_states={iid: True for iid in _ids(n_targets)},
    )
    for outcome in outcomes:
        assert outcome.resolution == Resolution.CLEAR


@pytest.mark.parametrize("n_targets", [1, 3])
def test_liveness_alive_resolves_in_flight(n_targets):
    record = _record(n_targets)
    outcomes = resolve_pending(
        record,
        now=1010.0,
        liveness=Liveness.ALIVE,
        helper_results={},
        device_states={},
    )
    for outcome in outcomes:
        assert outcome.resolution == Resolution.IN_FLIGHT
        assert outcome.can_force_unlock is False


@pytest.mark.parametrize("n_targets", [1, 3])
def test_elapsed_below_expiry_resolves_in_flight(n_targets):
    # Liveness.DEAD is a confirmed, conclusive signal (the helper will
    # never write a result), so it short-circuits to UNCONFIRMABLE even
    # while elapsed_s is still well under expiry_s -- it does not wait out
    # the rest of the window the way the ambiguous UNKNOWN case does.
    record = _record(n_targets, started_at=1000.0)
    outcomes = resolve_pending(
        record,
        now=1050.0,  # elapsed 50s < default expiry_s 120.0
        liveness=Liveness.DEAD,
        helper_results={},
        device_states={},
    )
    for outcome in outcomes:
        assert outcome.resolution == Resolution.UNCONFIRMABLE
        assert outcome.can_force_unlock is True
        assert outcome.elapsed_s == pytest.approx(50.0)


@pytest.mark.parametrize("n_targets", [1, 3])
def test_liveness_dead_resolves_unconfirmable_even_within_expiry(n_targets):
    # A confirmed-dead helper (PID-reuse-protected: it will NEVER write a
    # result) is at least as conclusive as a mere elapsed-time guess, so it
    # must short-circuit straight to UNCONFIRMABLE instead of waiting out
    # the rest of the expiry window as IN_FLIGHT.
    record = _record(n_targets, started_at=1000.0)
    outcomes = resolve_pending(
        record,
        now=1040.0,  # elapsed 40s, well under default expiry_s 120.0
        liveness=Liveness.DEAD,
        helper_results={},
        device_states={},
    )
    for outcome in outcomes:
        assert outcome.resolution == Resolution.UNCONFIRMABLE
        assert outcome.can_force_unlock is True


@pytest.mark.parametrize("n_targets", [1, 3])
def test_elapsed_past_expiry_resolves_unconfirmable(n_targets):
    record = _record(n_targets, started_at=1000.0)
    outcomes = resolve_pending(
        record,
        now=1200.0,  # elapsed 200s >= default expiry_s 120.0
        liveness=Liveness.DEAD,
        helper_results={},
        device_states={},
    )
    for outcome in outcomes:
        assert outcome.resolution == Resolution.UNCONFIRMABLE
        assert outcome.can_force_unlock is True


def test_expiry_boundary_exactly_at_expiry_is_unconfirmable():
    # elapsed_s == expiry_s must fall to UNCONFIRMABLE (strict < for IN_FLIGHT).
    record = _record(1, started_at=1000.0)
    outcomes = resolve_pending(
        record,
        now=1120.0,  # elapsed exactly 120.0 == default expiry_s
        liveness=Liveness.DEAD,
        helper_results={},
        device_states={},
        expiry_s=120.0,
    )
    assert outcomes[0].resolution == Resolution.UNCONFIRMABLE
    assert outcomes[0].can_force_unlock is True


def test_started_at_none_forces_elapsed_to_expiry_s():
    record = _record(1, started_at=None)
    outcomes = resolve_pending(
        record,
        now=1000.0,
        liveness=Liveness.DEAD,
        helper_results={},
        device_states={},
        expiry_s=120.0,
    )
    assert outcomes[0].elapsed_s == pytest.approx(120.0)
    assert outcomes[0].resolution == Resolution.UNCONFIRMABLE


@pytest.mark.parametrize("bad_started_at", ["not-a-number", [1, 2], {"a": 1}, True])
def test_started_at_non_numeric_does_not_raise_and_forces_elapsed_to_expiry_s(bad_started_at):
    # Defense in depth: a record reaching resolve_pending by some path other
    # than normalize_pending (which already type-guards started_at) could
    # still carry a non-numeric value. `now - started_at` must never raise
    # a TypeError -- the malformed value is treated the same as an absent
    # one, i.e. elapsed_s is forced to expiry_s rather than crashing or
    # being silently trusted as a valid, still-fresh timestamp.
    record = _record(1, started_at=bad_started_at)
    outcomes = resolve_pending(
        record,
        now=1000.0,
        liveness=Liveness.DEAD,
        helper_results={},
        device_states={},
        expiry_s=120.0,
    )
    assert outcomes[0].elapsed_s == pytest.approx(120.0)
    assert outcomes[0].resolution == Resolution.UNCONFIRMABLE


def test_mixed_outcomes_preserve_per_target_independence():
    # liveness=UNKNOWN here (not DEAD) so the third target -- which has no
    # helper_result or device_state of its own -- falls through to the
    # elapsed-time heuristic and stays IN_FLIGHT, keeping this test focused
    # on per-target independence rather than the DEAD short-circuit.
    record = _record(3, started_at=1000.0)
    ids = _ids(3)
    outcomes = resolve_pending(
        record,
        now=1050.0,  # elapsed 50s, below expiry
        liveness=Liveness.UNKNOWN,
        helper_results={ids[0]: (True, "Disabled"), ids[1]: (False, "Access denied")},
        device_states={},
    )
    assert len(outcomes) == 3
    by_id = {o.instance_id: o for o in outcomes}
    assert by_id[ids[0]].resolution == Resolution.DISABLED_CONFIRMED
    assert by_id[ids[1]].resolution == Resolution.FAILED
    assert by_id[ids[2]].resolution == Resolution.IN_FLIGHT


@pytest.mark.parametrize("n_targets", [1, 3])
def test_helper_ok_true_disagreeing_with_enabled_device_state_is_unconfirmable(n_targets):
    # Round 8 finding 1: a driver's CM_Disable_DevNode call can return
    # CR_SUCCESS without the device's actual state changing. When the helper
    # claims success but the observed device_state says the device is still
    # enabled, resolve_pending must NOT report DISABLED_CONFIRMED -- that
    # false-positive would clear the crash-recovery record for a monitor
    # that is, in truth, still on.
    record = _record(n_targets)
    ids = _ids(n_targets)
    outcomes = resolve_pending(
        record,
        now=1010.0,
        liveness=Liveness.DEAD,
        helper_results={iid: (True, "Disabled") for iid in ids},
        device_states={iid: True for iid in ids},  # still enabled, disagrees
    )
    assert len(outcomes) == n_targets
    for outcome in outcomes:
        assert outcome.resolution == Resolution.UNCONFIRMABLE
        assert outcome.resolution != Resolution.DISABLED_CONFIRMED
        assert outcome.can_force_unlock is True


def test_helper_ok_true_agreeing_with_disabled_device_state_stays_confirmed():
    # Sanity: when both signals AGREE (helper says success, device is
    # actually disabled), the existing confirmed-disable path is unchanged.
    record = _record(1)
    outcomes = resolve_pending(
        record,
        now=1010.0,
        liveness=Liveness.DEAD,
        helper_results={"id-0": (True, "Disabled")},
        device_states={"id-0": False},  # actually disabled, agrees
    )
    assert outcomes[0].resolution == Resolution.DISABLED_CONFIRMED
    assert outcomes[0].can_force_unlock is False


def test_helper_ok_true_with_unknown_device_state_still_confirmed():
    # When device_state can't be determined (None), there is nothing to
    # cross-check against, so the helper's own report is still trusted --
    # matches the "when BOTH are available" scope of the cross-check.
    record = _record(1)
    outcomes = resolve_pending(
        record,
        now=1010.0,
        liveness=Liveness.DEAD,
        helper_results={"id-0": (True, "Disabled")},
        device_states={"id-0": None},
    )
    assert outcomes[0].resolution == Resolution.DISABLED_CONFIRMED


@pytest.mark.parametrize("n_targets", [1, 3])
def test_helper_ok_false_agreeing_with_disabled_device_state_is_unconfirmable(n_targets):
    # Mirror of the True-branch cross-check: a helper-reported failure can
    # itself be spurious (a transient CfgMgr32 quirk, or the disable taking
    # effect a moment after the helper's own error return) while the device
    # is freshly observed as actually disabled. Trusting the helper's False
    # report unconditionally here would resolve to FAILED (can_force_unlock
    # False, not even eligible for the force-unlock escape hatch) for a
    # monitor that is, in truth, already disabled. This must resolve to
    # UNCONFIRMABLE, exactly like the reverse mismatch does.
    record = _record(n_targets)
    ids = _ids(n_targets)
    outcomes = resolve_pending(
        record,
        now=1010.0,
        liveness=Liveness.DEAD,
        helper_results={iid: (False, "Access denied") for iid in ids},
        device_states={iid: False for iid in ids},  # actually disabled, disagrees with failure
    )
    assert len(outcomes) == n_targets
    for outcome in outcomes:
        assert outcome.resolution == Resolution.UNCONFIRMABLE
        assert outcome.resolution != Resolution.FAILED
        assert outcome.can_force_unlock is True


@pytest.mark.parametrize(
    "device_state",
    [True, None],
    ids=["disagreeing_still_enabled", "unavailable"],
)
def test_helper_ok_false_not_agreeing_with_device_state_stays_failed(device_state):
    # Regression coverage for the real-failure case: when the observed
    # device_state genuinely disagrees with the helper's failure report (the
    # device is still enabled) or simply is not available to cross-check
    # against, the helper's False report is trusted as-is and resolves to
    # FAILED, matching the existing behavior before this cross-check.
    record = _record(1)
    outcomes = resolve_pending(
        record,
        now=1010.0,
        liveness=Liveness.DEAD,
        helper_results={"id-0": (False, "Access denied")},
        device_states={"id-0": device_state},
    )
    assert outcomes[0].resolution == Resolution.FAILED
    assert outcomes[0].can_force_unlock is False


def test_resolve_pending_is_pure_no_path_or_file_parameters():
    sig = inspect.signature(resolve_pending)
    assert set(sig.parameters) == {
        "record",
        "now",
        "liveness",
        "helper_results",
        "device_states",
        "expiry_s",
    }


def test_resolve_pending_has_zero_try_except_and_zero_io():
    source = inspect.getsource(resolve_pending)
    assert "try" not in source
    assert "open(" not in source
    assert "os.path" not in source

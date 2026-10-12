import os

import pytest

import quickres.monitors as monitors_mod
from quickres.monitors import process_liveness, sample_device_states
from quickres.recovery import Liveness


def test_owner_pid_mismatch_forces_unknown_even_if_probe_says_alive(monkeypatch):
    # REC-4: a mismatched owner_pid must force UNKNOWN even when the
    # underlying pid-liveness probe would otherwise report alive.
    monkeypatch.setattr(monitors_mod, "_is_pid_alive", lambda pid: True)

    result = process_liveness(helper_pid=4242, owner_pid=os.getpid() + 999999)

    assert result is Liveness.UNKNOWN


def test_matching_owner_and_alive_probe_returns_alive(monkeypatch):
    monkeypatch.setattr(monitors_mod, "_is_pid_alive", lambda pid: True)

    result = process_liveness(helper_pid=4242, owner_pid=os.getpid())

    assert result is Liveness.ALIVE


def test_matching_owner_and_dead_probe_returns_dead(monkeypatch):
    monkeypatch.setattr(monitors_mod, "_is_pid_alive", lambda pid: False)

    result = process_liveness(helper_pid=4242, owner_pid=os.getpid())

    assert result is Liveness.DEAD


def test_helper_pid_none_returns_unknown(monkeypatch):
    monkeypatch.setattr(
        monitors_mod, "_is_pid_alive", lambda pid: (_ for _ in ()).throw(
            AssertionError("must not probe when helper_pid is None")
        )
    )

    result = process_liveness(helper_pid=None, owner_pid=os.getpid())

    assert result is Liveness.UNKNOWN


# ---------------------------------------------------------------------------
# Round 18 R4: helper_pid PID-reuse identity guard (mirrors the owner_pid
# guard above). When a caller supplies helper_pid_start_time, a live-looking
# PID whose CURRENT creation time disagrees with the recorded one must not
# be trusted as the original helper -- Windows reuses PIDs quickly, so a
# bare "OpenProcess+GetExitCodeProcess says alive" is not proof of identity.
# ---------------------------------------------------------------------------


def test_helper_pid_reused_by_unrelated_process_forces_unknown(monkeypatch):
    # The raw liveness probe says "alive" (some process now holds this PID),
    # but its creation time does not match the one recorded when the real
    # helper was launched -- this is exactly PID reuse by an unrelated
    # process and must NOT be reported ALIVE.
    monkeypatch.setattr(monitors_mod, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(monitors_mod, "get_process_start_time", lambda pid: 999999)

    result = process_liveness(
        helper_pid=4242, owner_pid=os.getpid(), helper_pid_start_time=111111
    )

    assert result is Liveness.UNKNOWN


def test_helper_pid_matching_start_time_returns_alive(monkeypatch):
    monkeypatch.setattr(monitors_mod, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(monitors_mod, "get_process_start_time", lambda pid: 555555)

    result = process_liveness(
        helper_pid=4242, owner_pid=os.getpid(), helper_pid_start_time=555555
    )

    assert result is Liveness.ALIVE


def test_helper_pid_start_time_unresolvable_at_check_time_forces_unknown(monkeypatch):
    # The PID is reported alive but its creation time can no longer be
    # queried (e.g. it just exited, or access is denied) -- treat identity
    # as unconfirmed rather than trusting the bare liveness probe.
    monkeypatch.setattr(monitors_mod, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(monitors_mod, "get_process_start_time", lambda pid: None)

    result = process_liveness(
        helper_pid=4242, owner_pid=os.getpid(), helper_pid_start_time=555555
    )

    assert result is Liveness.UNKNOWN


def test_helper_pid_start_time_probe_oserror_forces_unknown(monkeypatch):
    monkeypatch.setattr(monitors_mod, "_is_pid_alive", lambda pid: True)

    def _boom(pid):
        raise OSError("access denied")

    monkeypatch.setattr(monitors_mod, "get_process_start_time", _boom)

    result = process_liveness(
        helper_pid=4242, owner_pid=os.getpid(), helper_pid_start_time=555555
    )

    assert result is Liveness.UNKNOWN


def test_no_start_time_supplied_preserves_legacy_alive_behavior(monkeypatch):
    # Callers that don't yet thread helper_pid_start_time through (existing
    # on-disk pending records with no such field) keep today's behavior --
    # the identity guard only activates when a start time is available to
    # check against.
    monkeypatch.setattr(monitors_mod, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        monitors_mod, "get_process_start_time", lambda pid: (_ for _ in ()).throw(
            AssertionError("must not probe start time when none was supplied")
        )
    )

    result = process_liveness(helper_pid=4242, owner_pid=os.getpid())

    assert result is Liveness.ALIVE


# ---------------------------------------------------------------------------
# Non-int helper_pid/owner_pid on a malformed on-disk record. A record read
# from pending_restore.json has no schema/type enforcement -- a non-int
# helper_pid passed straight into _is_pid_alive's ctypes OpenProcess call
# (declared with a DWORD argtype) raises ctypes.ArgumentError, which is NOT
# an OSError and previously was not caught anywhere in this call chain.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_helper_pid", ["4242", 3.14, [4242], {"pid": 4242}, True, False])
def test_helper_pid_non_int_returns_unknown_without_probing(monkeypatch, bad_helper_pid):
    monkeypatch.setattr(
        monitors_mod, "_is_pid_alive", lambda pid: (_ for _ in ()).throw(
            AssertionError("must not probe when helper_pid is not an int")
        )
    )

    result = process_liveness(helper_pid=bad_helper_pid, owner_pid=os.getpid())

    assert result is Liveness.UNKNOWN


def test_helper_pid_string_digits_does_not_raise_ctypes_argument_error(monkeypatch):
    # Regression guard for the real production seam (no monkeypatch on
    # _is_pid_alive): a string that merely *looks* like a pid number must
    # still be rejected by the type check rather than reaching ctypes.
    result = process_liveness(helper_pid="4242", owner_pid=os.getpid())

    assert result is Liveness.UNKNOWN


@pytest.mark.parametrize("bad_owner_pid", ["111", 3.14, [111], {"pid": 111}, True, False])
def test_owner_pid_non_int_returns_unknown_without_probing(monkeypatch, bad_owner_pid):
    monkeypatch.setattr(
        monitors_mod, "_is_pid_alive", lambda pid: (_ for _ in ()).throw(
            AssertionError("must not probe when owner_pid is not an int")
        )
    )

    result = process_liveness(helper_pid=4242, owner_pid=bad_owner_pid)

    assert result is Liveness.UNKNOWN


# ---------------------------------------------------------------------------
# T2.6 sample_device_states(instance_ids)
# ---------------------------------------------------------------------------


def test_sample_device_states_mix_of_enabled_disabled_undetermined(monkeypatch):
    monkeypatch.setattr(
        monitors_mod,
        "enumerate_monitors",
        lambda: [
            {"instance_id": "A", "friendly_name": "Mon A", "enabled": True},
            {"instance_id": "B", "friendly_name": "Mon B", "enabled": False},
            # "C" deliberately absent -- undetermined status was omitted
        ],
    )

    result = sample_device_states(["A", "B", "C"])

    assert result == {"A": True, "B": False, "C": None}

import dataclasses

import pytest

from quickres.recovery import Liveness, PendingOutcome, Resolution


def test_liveness_values():
    assert Liveness.ALIVE.value == "alive"
    assert Liveness.DEAD.value == "dead"
    assert Liveness.UNKNOWN.value == "unknown"


def test_resolution_values():
    assert Resolution.CLEAR.value == "clear"
    assert Resolution.DISABLED_CONFIRMED.value == "disabled_confirmed"
    assert Resolution.FAILED.value == "failed"
    assert Resolution.IN_FLIGHT.value == "in_flight"
    assert Resolution.UNCONFIRMABLE.value == "unconfirmable"
    assert Resolution.UNLOCKED_UNCONFIRMED.value == "unlocked_unconfirmed"


def test_pending_outcome_is_frozen():
    outcome = PendingOutcome(
        resolution=Resolution.CLEAR,
        instance_id="DISPLAY\\ACR0123\\4&abc&0&UID256",
        friendly_name="Acer XV272",
        message="Disabled",
        elapsed_s=1.5,
        can_force_unlock=False,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.message = "mutated"

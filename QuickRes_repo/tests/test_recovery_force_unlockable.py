from quickres.recovery import PendingOutcome, Resolution, force_unlockable


def _outcome(resolution, instance_id="id-0"):
    return PendingOutcome(
        resolution=resolution,
        instance_id=instance_id,
        friendly_name="Monitor",
        message="msg",
        elapsed_s=1.0,
        can_force_unlock=(resolution == Resolution.UNCONFIRMABLE),
    )


def test_all_unconfirmable_is_force_unlockable():
    outcomes = [_outcome(Resolution.UNCONFIRMABLE, "id-0"), _outcome(Resolution.UNCONFIRMABLE, "id-1")]
    assert force_unlockable(outcomes) is True


def test_in_flight_among_unconfirmable_blocks_force_unlock():
    outcomes = [
        _outcome(Resolution.UNCONFIRMABLE, "id-0"),
        _outcome(Resolution.IN_FLIGHT, "id-1"),
    ]
    assert force_unlockable(outcomes) is False


def test_all_disabled_confirmed_is_not_force_unlockable():
    outcomes = [_outcome(Resolution.DISABLED_CONFIRMED, "id-0"), _outcome(Resolution.DISABLED_CONFIRMED, "id-1")]
    assert force_unlockable(outcomes) is False


def test_empty_list_is_not_force_unlockable():
    assert force_unlockable([]) is False

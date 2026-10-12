"""Pure crash-recovery ladder for pending monitor operations.

This module MUST NOT import ctypes, MUST NOT import quickres.monitors, and
MUST NOT perform any I/O or default any `now` parameter to time.time() —
callers always supply `now` explicitly. This purity is what makes the
resolution ladder table-driven testable.
"""

import os
import re
from dataclasses import dataclass
from enum import Enum

# The single naming convention every `monitor_op_result_<pid>_<ms>.json`
# path derives from -- monitors.py's make_result_filename() generates
# names with these, this module's own regex validates against them, and
# monitors.py's _cleanup_stale_result_files sweep matches against them, so
# there is exactly one place that owns the literal strings.
RESULT_FILE_PREFIX = "monitor_op_result_"
RESULT_FILE_SUFFIX = ".json"

# A short-lived, elevated helper uses these two strictly-scoped files while
# a monitor-disable confirmation is open.  The command file may only request
# keeping or reverting the exact monitor ids that helper already disabled;
# it never carries a new privileged operation or an arbitrary path.
GUARD_COMMAND_FILE_PREFIX = "monitor_guard_command_"
GUARD_RESULT_FILE_PREFIX = "monitor_guard_result_"
GUARD_FILE_SUFFIX = ".json"

_RESULT_FILENAME_RE = re.compile(
    rf"^{re.escape(RESULT_FILE_PREFIX)}\d+_\d+{re.escape(RESULT_FILE_SUFFIX)}$"
)
_GUARD_COMMAND_FILENAME_RE = re.compile(
    rf"^{re.escape(GUARD_COMMAND_FILE_PREFIX)}\d+_\d+{re.escape(GUARD_FILE_SUFFIX)}$"
)
_GUARD_RESULT_FILENAME_RE = re.compile(
    rf"^{re.escape(GUARD_RESULT_FILE_PREFIX)}\d+_\d+{re.escape(GUARD_FILE_SUFFIX)}$"
)


class Liveness(str, Enum):
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


class Resolution(str, Enum):
    CLEAR = "clear"
    DISABLED_CONFIRMED = "disabled_confirmed"
    FAILED = "failed"
    IN_FLIGHT = "in_flight"
    UNCONFIRMABLE = "unconfirmable"
    UNLOCKED_UNCONFIRMED = "unlocked_unconfirmed"


@dataclass(frozen=True)
class PendingOutcome:
    resolution: Resolution
    instance_id: str | None
    friendly_name: str
    message: str
    elapsed_s: float
    can_force_unlock: bool


def normalize_pending(raw: dict | None, *, mtime: float) -> dict | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("action") != "disable":
        return None

    targets = raw.get("targets")
    if not isinstance(targets, list) or not targets:
        return None

    normalized_targets = []
    for target in targets:
        if not isinstance(target, dict):
            return None
        instance_id = target.get("instance_id")
        if not instance_id or not isinstance(instance_id, str):
            return None
        normalized_targets.append(
            {
                **target,
                "instance_id": instance_id,
                "friendly_name": target.get("friendly_name") or "",
            }
        )

    normalized = dict(raw)
    normalized["targets"] = normalized_targets
    raw_started_at = raw.get("started_at")
    # `raw.get("started_at") or mtime` alone only checks truthiness, not
    # type -- a truthy but non-numeric value on disk (e.g. from disk
    # corruption that still parses as valid JSON, a partial write from an
    # older build, or manual editing) would otherwise pass straight through
    # and later blow up resolve_pending's `now - started_at` with a
    # TypeError. Falling back to `mtime` for a non-numeric value keeps this
    # consistent with the same fallback already used when the field is
    # simply absent, so a malformed value degrades the same way a missing
    # one already does instead of crashing further downstream.
    started_at_is_numeric = isinstance(raw_started_at, (int, float)) and not isinstance(
        raw_started_at, bool
    )
    normalized["started_at"] = raw_started_at if started_at_is_numeric and raw_started_at else mtime
    normalized["unlocked_at"] = raw.get("unlocked_at")
    return normalized


def resolve_pending(
    record: dict,
    *,
    now: float,
    liveness: Liveness,
    helper_results: dict,
    device_states: dict,
    expiry_s: float = 120.0,
) -> list:
    started_at = record.get("started_at")
    # Defense in depth alongside normalize_pending's own type gate: a record
    # reaching this function by some other path (a direct caller that never
    # ran it through normalize_pending) could still carry a non-numeric
    # started_at. Treating that the same as an absent value -- falling
    # straight to the already-existing `elapsed_s = expiry_s` fallback below
    # -- avoids a TypeError from `now - started_at` without asserting a
    # malformed record is actually still in flight.
    if isinstance(started_at, bool) or not isinstance(started_at, (int, float)):
        started_at = None
    elapsed_s = expiry_s if started_at is None else max(0.0, now - started_at)
    # `unlocked_at` is stamped per-target (see bridge.py's
    # `force_unlock_pending`), not just at the record level, so that force-
    # unlocking one target does not also unlock every other target in the
    # same record. The record-level field is still checked here too, purely
    # for backward compatibility with an already-on-disk record written
    # before per-target stamping existed (or by any other legacy path that
    # only ever set it record-wide); a record-level value still unlocks
    # every target in it, matching that older behavior exactly.
    record_unlocked_at = record.get("unlocked_at")

    outcomes = []
    for target in record["targets"]:
        instance_id = target["instance_id"]
        friendly_name = target.get("friendly_name") or ""
        helper_result = helper_results.get(instance_id)
        device_state = device_states.get(instance_id)
        target_unlocked_at = target.get("unlocked_at")

        if target_unlocked_at is not None or record_unlocked_at is not None:
            resolution = Resolution.UNLOCKED_UNCONFIRMED
            message = "Unlocked, outcome unconfirmed"
            can_force_unlock = False
        # Cross-check: a driver's CM_Disable_DevNode call can return
        # CR_SUCCESS without the device's actual state ever changing, so a
        # helper report of ok=True is only trusted when a fresh observed
        # device_state is either unavailable (nothing to check) or agrees
        # with it (device_state is not True, i.e. actually disabled). When
        # both are available and they disagree, this reuses UNCONFIRMABLE
        # rather than falsely reporting DISABLED_CONFIRMED.
        elif (
            helper_result is not None
            and helper_result[0] is True
            and device_state is not None
            and device_state is not False
        ):
            resolution = Resolution.UNCONFIRMABLE
            message = "Could not confirm — helper reported success but device is still enabled"
            can_force_unlock = True
        elif helper_result is not None and helper_result[0] is True:
            resolution = Resolution.DISABLED_CONFIRMED
            message = "Disabled"
            can_force_unlock = False
        # Mirror of the cross-check above for the opposite direction: a
        # helper-reported failure can itself be spurious (a transient
        # CfgMgr32 quirk, or the disable taking effect a moment after the
        # helper's own result write reported an error) while device_state
        # actually agrees the device reached the requested state (disabled).
        # Trusting a False report unconditionally here would resolve to
        # FAILED -- not even eligible for the force-unlock escape hatch --
        # for a target that is, in truth, already disabled, so this reuses
        # UNCONFIRMABLE the same way the reverse mismatch already does.
        elif (
            helper_result is not None
            and helper_result[0] is False
            and device_state is not None
            and device_state is False
        ):
            resolution = Resolution.UNCONFIRMABLE
            message = "Could not confirm — helper reported failure but device is disabled"
            can_force_unlock = True
        elif helper_result is not None and helper_result[0] is False:
            resolution = Resolution.FAILED
            message = f"Failed: {helper_result[1]}"
            can_force_unlock = False
        elif device_state is False:
            resolution = Resolution.DISABLED_CONFIRMED
            message = "Disabled"
            can_force_unlock = False
        elif device_state is True:
            resolution = Resolution.CLEAR
            message = "Clear"
            can_force_unlock = False
        elif liveness == Liveness.ALIVE:
            resolution = Resolution.IN_FLIGHT
            message = "Still in progress"
            can_force_unlock = False
        # A confirmed-dead helper will never write a result, so it is at
        # least as conclusive as a plain elapsed-time guess -- it resolves
        # immediately below rather than waiting for elapsed_s to reach
        # expiry_s like the still-ambiguous Liveness.UNKNOWN case does.
        elif liveness != Liveness.DEAD and elapsed_s < expiry_s:
            resolution = Resolution.IN_FLIGHT
            message = "Still in progress"
            can_force_unlock = False
        else:
            resolution = Resolution.UNCONFIRMABLE
            message = "Could not confirm — helper is gone"
            can_force_unlock = True

        outcomes.append(
            PendingOutcome(
                resolution=resolution,
                instance_id=instance_id,
                friendly_name=friendly_name,
                message=message,
                elapsed_s=elapsed_s,
                can_force_unlock=can_force_unlock,
            )
        )

    return outcomes


def force_unlockable(outcomes: list) -> bool:
    if not outcomes:
        return False
    has_in_flight = any(o.resolution == Resolution.IN_FLIGHT for o in outcomes)
    has_unconfirmable = any(o.resolution == Resolution.UNCONFIRMABLE for o in outcomes)
    return not has_in_flight and has_unconfirmable


def is_safe_result_path(path: str, app_dir: str) -> bool:
    if '"' in path or "'" in path:
        return False
    if not _RESULT_FILENAME_RE.match(os.path.basename(path)):
        return False
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(app_dir):
        return False
    return True


def _is_safe_guard_path(path: str, app_dir: str, filename_re) -> bool:
    if '"' in path or "'" in path:
        return False
    if not filename_re.match(os.path.basename(path)):
        return False
    return os.path.dirname(os.path.abspath(path)) == os.path.abspath(app_dir)


def is_safe_guard_command_path(path: str, app_dir: str) -> bool:
    return _is_safe_guard_path(path, app_dir, _GUARD_COMMAND_FILENAME_RE)


def is_safe_guard_result_path(path: str, app_dir: str) -> bool:
    return _is_safe_guard_path(path, app_dir, _GUARD_RESULT_FILENAME_RE)

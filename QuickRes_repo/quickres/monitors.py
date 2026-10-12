import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
import re
import sys
import threading
import time
import uuid

from quickres import recovery
from quickres.config import APP_DIR, load_pending, log_msg, write_json_atomic

user32 = ctypes.windll.user32
setupapi = ctypes.windll.setupapi
cfgmgr32 = ctypes.windll.cfgmgr32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

GUID_DEVCLASS_MONITOR = "4d36e96e-e325-11ce-bfc1-08002be10318"

DIGCF_PRESENT = 0x00000002
SPDRP_DEVICEDESC = 0x00000000
SPDRP_FRIENDLYNAME = 0x0000000C
CR_SUCCESS = 0
CM_PROB_DISABLED = 22
CM_LOCATE_DEVNODE_NORMAL = 0
CM_DRP_CLASSGUID = 0x00000009
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
WAIT_OBJECT_0 = 0x00000000


@dataclass
class GuardedDisableSession:
    """One already-elevated helper, limited to the confirmation window.

    Its command channel deliberately allows only ``keep`` or ``revert`` for
    the monitor ids that this exact helper has just disabled.  It is not a
    reusable elevation token and cannot execute arbitrary operations.
    """

    instance_ids: tuple[str, ...]
    command_path: str
    completion_path: str
    app_dir: str
    completion_timeout_s: float = 10.0
    command_requested: bool = False


@dataclass
class _GuardedDisableContext:
    timeout_s: float
    session: GuardedDisableSession | None = None


_guarded_disable_local = threading.local()


@contextmanager
def guarded_disable_session(timeout_s: float = 10.0):
    """Opt a single disable call into the short-lived elevated guard.

    This preserves ``set_monitors_enabled``'s public call shape, including
    its existing test and integration seams, while keeping the special mode
    explicit at the bridge boundary that owns the confirmation timer.
    """
    previous = getattr(_guarded_disable_local, "context", None)
    context = _GuardedDisableContext(timeout_s=float(timeout_s))
    _guarded_disable_local.context = context
    try:
        yield context
    finally:
        _guarded_disable_local.context = previous

# SetupAPI instance IDs are vendor/EDID-derived (e.g.
# "DISPLAY\DEL4110\5&2e2fefea&0&UID1078018") and normally only ever contain
# these characters. This is interpolated into a double-quoted elevated-helper
# command line (_build_helper_params) with no escaping, so a value outside
# this shape is refused rather than risking a quote-breaking/argv-corrupting
# injection into an admin-privileged process launch.
#
# The final character is deliberately restricted to a NON-backslash: a
# trailing backslash (odd OR even run length) breaks Windows argv quoting
# once interpolated into `--instance-id "{id}"` followed by
# `--result-file "{path}"` (verified empirically via CommandLineToArgvW --
# a trailing backslash immediately before the closing `"` gets consumed as
# a literal, swallowing the next argument). A real device instance id never
# legitimately ends in a backslash, so forbidding it outright is the
# simplest safe fix.
_SAFE_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9_&\\]*[A-Za-z0-9_&]$")

# Distinct from an ordinary failure: WaitForSingleObject timing out means the
# elevated process (likely still waiting on a slow UAC prompt) is UNKNOWN,
# not failed — it could still disable/enable the device moments later, off
# in the background, after this function has already returned. Callers must
# not treat this the same as a confirmed failure (e.g. must not clear a
# crash-recovery flag on the strength of it).
TIMEOUT_MESSAGE = "Elevated operation timed out (still running in the background, outcome unknown)"

# Prefix for the "device no longer resolvable" failure (unplugged, replaced,
# or its instance id changed since the flag was written). Unlike other
# failures, this one means there is nothing left to protect against, so
# callers may treat it as safe to clear a stale crash-recovery flag over
# rather than leaving the user permanently stuck retrying.
DEVICE_NOT_FOUND_PREFIX = "Could not locate device"

# The helper process and the freshly observed device
# state are two independent signals for the same outcome. A driver's
# CM_Disable_DevNode/CM_Enable_DevNode call can return CR_SUCCESS without
# the device's actual enabled/disabled state ever changing, so a helper
# report of ok=True is only trusted when the observed state agrees with it
# (or when no observed state is available to check against at all). When
# both are available and they disagree, the outcome is unconfirmed rather
# than a false-positive success.
HELPER_OBSERVED_MISMATCH_MESSAGE = (
    "Helper reported success but observed device state disagrees (outcome unconfirmed)"
)

# Mirror of the cross-check above for the opposite direction: a
# helper-reported failure can itself be spurious (a transient CfgMgr32
# quirk, or the disable/enable taking effect a moment after the helper's own
# result write reported an error) while the freshly observed device state
# actually shows the requested `enabled` value was reached. A helper report
# of ok=False is therefore only trusted when the observed state agrees with
# it (or when no observed state is available to check against at all). When
# both are available and they disagree, the outcome is unconfirmed rather
# than a genuine failure -- otherwise a caller could discard the
# crash-recovery record and skip the auto-revert guard for a monitor that
# is, in truth, still in its pre-op state.
HELPER_OBSERVED_FAILURE_MISMATCH_MESSAGE = (
    "Helper reported failure but observed device state disagrees (outcome unconfirmed)"
)

# The elevated helper can exit within the wait timeout (so this is distinct
# from TIMEOUT_MESSAGE) yet still fail to persist its own result file -- for
# example write_json_atomic failing under disk-full or permission-denied
# conditions inside the elevated process, after its underlying
# CM_Disable_DevNode/CM_Enable_DevNode call may have already genuinely
# succeeded. When that happens AND the fresh device-state re-check
# (sample_device_states) also can't determine the device's current state,
# there is no signal left to confirm the outcome either way, so it must be
# treated as unknown rather than a confirmed failure -- the same way
# TIMEOUT_MESSAGE already is by downstream callers (e.g.
# webview/bridge.py's _finalize_disable_outcome), so the crash-recovery
# record and auto-revert guard for it are not discarded on the strength of
# a result file that simply never got written.
HELPER_RESULT_UNCONFIRMED_MESSAGE = (
    "Elevated helper did not report a result and its outcome could not be "
    "confirmed by device state (outcome unknown)"
)

# Explicit per-target outcome classification, carried as the 4th element of
# every result tuple `set_monitors_enabled` returns (alongside the
# human-readable `message`, which stays display/logging text only). A
# caller that needs to know whether a result counts as a genuine failure or
# an unconfirmed/ambiguous outcome reads this field directly instead of
# comparing `message` against sentinel constants -- a new message added to
# the branches below always carries its own kind right where it is
# produced, so there is nothing else to keep in sync at each call site.
OUTCOME_CONFIRMED = "confirmed"
OUTCOME_GENUINE_FAILURE = "genuine_failure"
OUTCOME_AMBIGUOUS = "ambiguous"


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _make_guid(guid_str: str) -> GUID:
    u = uuid.UUID(guid_str)
    g = GUID()
    ctypes.memmove(ctypes.byref(g), u.bytes_le, 16)
    return g


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hKeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def is_valid_instance_id(instance_id: str) -> bool:
    """Allowlist gate for a Windows device instance id before it is
    interpolated, unescaped, into an elevated-helper command line.
    """
    if not instance_id:
        return False
    return bool(_SAFE_INSTANCE_ID_RE.match(instance_id))


# HDEVINFO is a pointer-sized opaque handle. ctypes' default restype (c_int,
# 32-bit) would truncate/corrupt it on 64-bit Windows, and passing it back
# into later calls without a declared argtype risks an OverflowError since
# the raw address is usually well outside the 32-bit range. Declare explicit
# prototypes for every SetupAPI/cfgmgr32 entry point that touches an HDEVINFO
# or DEVINST handle so these stay correct on 64-bit builds.
setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
]
setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInfo.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA),
]
setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA),
    wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]
setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

cfgmgr32.CM_Get_DevNode_Status.restype = wintypes.DWORD
cfgmgr32.CM_Get_DevNode_Status.argtypes = [
    ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG),
    wintypes.DWORD, wintypes.ULONG,
]
cfgmgr32.CM_Locate_DevNodeW.restype = wintypes.DWORD
cfgmgr32.CM_Locate_DevNodeW.argtypes = [
    ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR, wintypes.ULONG,
]
cfgmgr32.CM_Enable_DevNode.restype = wintypes.DWORD
cfgmgr32.CM_Enable_DevNode.argtypes = [wintypes.DWORD, wintypes.ULONG]
cfgmgr32.CM_Disable_DevNode.restype = wintypes.DWORD
cfgmgr32.CM_Disable_DevNode.argtypes = [wintypes.DWORD, wintypes.ULONG]

cfgmgr32.CM_Get_DevNode_Registry_PropertyW.restype = wintypes.DWORD
cfgmgr32.CM_Get_DevNode_Registry_PropertyW.argtypes = [
    wintypes.DWORD, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
    ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG,
]

shell32.ShellExecuteExW.restype = wintypes.BOOL
shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]

kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]

kernel32.GetProcessId.restype = wintypes.DWORD
kernel32.GetProcessId.argtypes = [wintypes.HANDLE]

kernel32.GetProcessTimes.restype = wintypes.BOOL
kernel32.GetProcessTimes.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259


def _normalize_interface_id(device_id: str) -> str:
    """Convert an EnumDisplayDevices interface path into a SetupAPI instance id.

    e.g. ``\\\\?\\DISPLAY#DEL4110#5&2e2fefea&0&UID1078018#{guid}``
    -> ``DISPLAY\\DEL4110\\5&2e2fefea&0&UID1078018``
    """
    s = device_id
    if s.startswith("\\\\?\\"):
        s = s[4:]
    # Strip the trailing interface-class GUID segment, e.g. #{...}
    hash_index = s.rfind("#{")
    if hash_index != -1 and s.endswith("}"):
        s = s[:hash_index]
    return s.replace("#", "\\")


def _list_raw_monitor_devices() -> list:
    """Enumerate raw (instance_id, friendly_name, devinst) tuples via
    SetupAPI. This is the only place that owns the SetupDiGetClassDevsW /
    SetupDiEnumDeviceInfo loop; it is not itself unit tested (it drives live
    Win32 APIs). `enumerate_monitors()` below is the injectable seam
    boundary tests exercise.
    """
    raw = []
    class_guid = _make_guid(GUID_DEVCLASS_MONITOR)
    hdevinfo = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(class_guid), None, None, DIGCF_PRESENT
    )
    if hdevinfo == INVALID_HANDLE_VALUE or not hdevinfo:
        return raw

    try:
        index = 0
        devinfo = SP_DEVINFO_DATA()
        devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
        while setupapi.SetupDiEnumDeviceInfo(hdevinfo, index, ctypes.byref(devinfo)):
            instance_id = _get_device_instance_id(hdevinfo, devinfo)
            friendly_name = _get_device_property(hdevinfo, devinfo, SPDRP_FRIENDLYNAME)
            if not friendly_name:
                friendly_name = _get_device_property(hdevinfo, devinfo, SPDRP_DEVICEDESC)
            if not friendly_name:
                friendly_name = instance_id or "Unknown monitor"

            raw.append((instance_id, friendly_name, devinfo.DevInst))

            index += 1
            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdevinfo)

    return raw


def _devnode_enabled(devinst) -> bool:
    """Injectable seam wrapping `_is_devnode_enabled`. Raises OSError when
    the device's enabled/disabled status cannot be determined right now.
    Named by dropping `_is_devnode_enabled`'s `is_` prefix, matching the
    prefix-drop convention `_get_devnode_class_guid` -> `_devnode_class_guid`
    uses for the same raw-call/seam pairing below.
    """
    return _is_devnode_enabled(devinst)


def enumerate_monitors() -> list:
    """Public enumeration surface:
    `[{"instance_id": str, "friendly_name": str, "enabled": bool}]`.
    A device whose status can't be determined is omitted entirely, never
    defaulted to enabled -- a safe-omit default rather than a guess.
    """
    monitors = []
    for instance_id, friendly_name, devinst in _list_raw_monitor_devices():
        try:
            enabled = _devnode_enabled(devinst)
        except OSError:
            continue
        monitors.append(
            {"instance_id": instance_id, "friendly_name": friendly_name, "enabled": enabled}
        )
    return monitors


def _is_pid_alive(pid: int) -> bool:
    """Injectable seam probing whether `pid` is a live, still-running
    process. Best-effort: an unopenable handle is treated as not-alive.
    """
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def get_process_start_time(pid: int) -> int | None:
    """Best-effort process-identity probe: the process's creation time
    (Windows `GetProcessTimes`), collapsed into a single 64-bit FILETIME
    integer. Returns None when the process can't be opened or queried right
    now -- callers must treat that as "identity unconfirmed", never as
    "matches" or "doesn't match".

    This is the counterpart to `_is_pid_alive`'s bare "does this PID number
    currently exist" check: a PID number alone is not a stable identity on
    Windows, which recycles PIDs quickly, so a caller that needs to confirm
    it is still looking at the SAME process it originally observed (not a
    different, unrelated process that has since been assigned the same PID)
    should capture this value once at the moment of first observation and
    compare it again later via this same function.
    """
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel_time), ctypes.byref(user_time),
        )
        if not ok:
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def process_liveness(helper_pid, owner_pid: int, helper_pid_start_time: int | None = None):
    """A stored owner_pid that no longer matches the current
    process's pid forces UNKNOWN unconditionally (PID-reuse guard) --
    this is the composition point `resolve_pending` (recovery.py)
    deliberately left to its caller.

    `_is_pid_alive`'s raw OpenProcess+GetExitCodeProcess probe
    on `helper_pid` only proves some process currently holds that PID
    number -- not that it is the SAME process originally launched as the
    helper. Windows recycles PIDs quickly, so between the helper's PID being
    captured and this check running later (e.g. across `recheck_pending`/
    `recover_on_boot` after a crash), an unrelated process can have been
    assigned that exact PID. When the caller supplies `helper_pid_start_time`
    (the creation time captured alongside `helper_pid` when it was first
    recorded, via `get_process_start_time`), a "PID exists" result is only
    trusted as ALIVE once its CURRENT creation time is re-queried and found
    to match -- any mismatch, or a creation time that can no longer be
    queried at all, forces UNKNOWN rather than a false ALIVE, mirroring the
    owner_pid guard above. When no `helper_pid_start_time` is supplied (the
    caller has nothing recorded to compare against -- e.g. an older pending
    record predating this field), this guard is skipped and behavior is
    unchanged from before.

    `helper_pid`/`owner_pid` both come straight off an on-disk record with
    no schema enforcement on read, so either can in principle be some
    non-int value (disk corruption that still parses as valid JSON, a
    partial write from an older build, manual editing). `owner_pid` is only
    ever compared with `!=`, which is already type-safe -- a mismatched
    type simply compares unequal and falls into the same UNKNOWN branch a
    mismatched pid number would, so its explicit type check below documents
    that safety rather than changing behavior. `helper_pid` is different: it
    gets passed straight into `_is_pid_alive`'s ctypes `OpenProcess` call,
    which is declared with a `DWORD` argtype and raises
    `ctypes.ArgumentError` for a non-int argument -- an exception this
    function's own `except OSError` does not catch. A non-int `helper_pid`
    is therefore treated the same way an unopenable/unqueryable process
    already is elsewhere in this function: as liveness that cannot be
    determined, not as a crash.
    """
    if not isinstance(owner_pid, int) or isinstance(owner_pid, bool) or owner_pid != os.getpid():
        return recovery.Liveness.UNKNOWN
    if helper_pid is None:
        return recovery.Liveness.UNKNOWN
    if not isinstance(helper_pid, int) or isinstance(helper_pid, bool):
        return recovery.Liveness.UNKNOWN
    try:
        alive = _is_pid_alive(helper_pid)
    except OSError:
        return recovery.Liveness.UNKNOWN
    if not alive:
        return recovery.Liveness.DEAD
    if helper_pid_start_time is not None:
        try:
            current_start_time = get_process_start_time(helper_pid)
        except OSError:
            return recovery.Liveness.UNKNOWN
        if current_start_time is None or current_start_time != helper_pid_start_time:
            return recovery.Liveness.UNKNOWN
    return recovery.Liveness.ALIVE


def sample_device_states(instance_ids: list) -> dict:
    """Current enabled/disabled state per id; `None` when undetermined
    (feeds `device_states` in `recovery.resolve_pending`, consistent with
    `enumerate_monitors`'s "omit" semantics translated to "unknown" at this
    dict level).
    """
    current = {m["instance_id"]: m["enabled"] for m in enumerate_monitors()}
    return {instance_id: current.get(instance_id) for instance_id in instance_ids}


def _build_helper_params(op: str, instance_ids: list, result_path: str) -> str:
    """CLI shape: no --batch flag; every instance id is repeated as its
    own `--instance-id <id>` occurrence, regardless of N.
    """
    parts = [f"--monitor-op {op}"]
    for instance_id in instance_ids:
        parts.append(f'--instance-id "{instance_id}"')
    parts.append(f'--result-file "{result_path}"')
    params = " ".join(parts)

    if getattr(sys, "frozen", False):
        return params
    script_path = os.path.abspath(sys.argv[0])
    return f'"{script_path}" {params}'


def _build_guarded_disable_params(
    instance_ids: list,
    result_path: str,
    command_path: str,
    completion_path: str,
    guard_timeout_s: float,
) -> str:
    """Arguments for the narrowly-scoped elevated confirmation helper."""
    parts = ["--monitor-op guarded-disable"]
    for instance_id in instance_ids:
        parts.append(f'--instance-id "{instance_id}"')
    parts.extend((
        f'--result-file "{result_path}"',
        f'--guard-command-file "{command_path}"',
        f'--guard-result-file "{completion_path}"',
        f"--guard-timeout-s {guard_timeout_s:.3f}",
    ))
    params = " ".join(parts)
    if getattr(sys, "frozen", False):
        return params
    script_path = os.path.abspath(sys.argv[0])
    return f'"{script_path}" {params}'


def _launch_elevated_helper(params: str):
    """Injectable seam around ShellExecuteExW `runas`. Returns an opaque
    process handle for `_wait_for_helper`, or None if elevation was
    cancelled/failed (e.g. UAC decline).
    """
    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = "runas"
    sei.lpFile = sys.executable
    sei.lpParameters = params
    sei.lpDirectory = None
    sei.nShow = SW_HIDE

    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        return None
    return sei.hProcess


def _wait_for_helper(handle, timeout_s: float) -> bool:
    """Injectable seam around WaitForSingleObject. Returns True only if the
    elevated process completed within `timeout_s`.
    """
    wait_result = kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))
    kernel32.CloseHandle(handle)
    return wait_result == WAIT_OBJECT_0


def make_result_filename(pid: int | None = None, ms: int | None = None) -> str:
    """Canonical `monitor_op_result_<pid>_<ms>.json` name -- the single
    generator every caller that needs a fresh result-file path uses
    (webview/bridge.py's Api.set_monitors_enabled pre-computes one so it
    can persist a crash-recovery record referencing this same path before
    elevation starts; this module's own set_monitors_enabled falls back to
    it when no result_path is supplied). `recovery.RESULT_FILE_PREFIX`/
    `RESULT_FILE_SUFFIX` are the same two literals `_cleanup_stale_result_files`
    matches filenames against and `recovery.is_safe_result_path`'s regex
    validates them against, so all three concerns stay derived from one
    shared naming convention instead of four independent copies that could
    silently drift apart.
    """
    pid = os.getpid() if pid is None else pid
    ms = int(time.time() * 1000) if ms is None else ms
    return f"{recovery.RESULT_FILE_PREFIX}{pid}_{ms}{recovery.RESULT_FILE_SUFFIX}"


def make_guard_filename(kind: str, pid: int | None = None, ms: int | None = None) -> str:
    """Return a canonical command/result filename for one guard session."""
    prefixes = {
        "command": recovery.GUARD_COMMAND_FILE_PREFIX,
        "result": recovery.GUARD_RESULT_FILE_PREFIX,
    }
    if kind not in prefixes:
        raise ValueError(f"Unknown guard filename kind: {kind!r}")
    pid = os.getpid() if pid is None else pid
    ms = int(time.time() * 1000) if ms is None else ms
    return f"{prefixes[kind]}{pid}_{ms}{recovery.GUARD_FILE_SUFFIX}"


def _assemble_operation_results(instance_ids: list, enabled: bool, helper_data, completed: bool) -> list:
    """Cross-check a helper report with the current device state.

    Both the ordinary one-shot helper and the temporary confirmation helper
    use this one outcome policy, so neither path can accidentally report a
    raw elevated return code as a confirmed device state.
    """
    helper_results = {}
    if helper_data:
        for entry in helper_data.get("results", []):
            entry_id = entry.get("instance_id")
            if entry_id:
                helper_results[entry_id] = (bool(entry.get("ok", False)), entry.get("message", ""))

    observed = sample_device_states(instance_ids)
    results = []
    for instance_id in instance_ids:
        helper_result = helper_results.get(instance_id)
        observed_state = observed.get(instance_id)
        if helper_result is not None:
            helper_ok, helper_message = helper_result
            if helper_ok and observed_state is not None and observed_state != enabled:
                ok, message, kind = False, HELPER_OBSERVED_MISMATCH_MESSAGE, OUTCOME_AMBIGUOUS
            elif not helper_ok and observed_state is not None and observed_state == enabled:
                ok, message, kind = False, HELPER_OBSERVED_FAILURE_MISMATCH_MESSAGE, OUTCOME_AMBIGUOUS
            else:
                ok, message = helper_ok, helper_message
                kind = OUTCOME_CONFIRMED if helper_ok else OUTCOME_GENUINE_FAILURE
        elif observed_state is not None and observed_state == enabled:
            ok, message, kind = True, "Confirmed by observed device state", OUTCOME_CONFIRMED
        elif not completed:
            ok, message, kind = False, TIMEOUT_MESSAGE, OUTCOME_AMBIGUOUS
        elif observed_state is None:
            ok, message, kind = False, HELPER_RESULT_UNCONFIRMED_MESSAGE, OUTCOME_AMBIGUOUS
        else:
            ok, message, kind = False, "Elevated helper did not report a result", OUTCOME_GENUINE_FAILURE
        results.append((instance_id, ok, message, kind))
    return results


def set_monitors_enabled(
    instance_ids: list,
    enabled: bool,
    *,
    app_dir=None,
    timeout_s: float = 30.0,
    result_path: str | None = None,
    on_helper_launched=None,
) -> list:
    """Uniform-N elevation path: one entry per id, in input order, one
    elevated helper launch total regardless of N. Every id is validated
    against the injection allowlist before any elevation is attempted; one
    unsafe id aborts the whole operation pre-elevation. The final
    per-id result combines the helper's own report with a fresh observed
    device-state re-check -- the raw return code is never trusted alone:
    when a fresh observed state is available for that id, it is cross-checked
    against the helper's report in both directions: a helper-claimed success
    (ok=True) that the observed state contradicts is downgraded to an
    unconfirmed outcome instead of a false-positive confirmation (see
    HELPER_OBSERVED_MISMATCH_MESSAGE), and symmetrically a helper-claimed
    failure (ok=False) that the observed state actually shows reached the
    requested `enabled` value is likewise downgraded to an unconfirmed
    outcome instead of a false-positive genuine failure (see
    HELPER_OBSERVED_FAILURE_MISMATCH_MESSAGE). When no observed state is
    available for an id, the helper's report is trusted as-is since there is
    nothing to cross-check it against.

    `result_path`, when supplied, lets a caller (webview/bridge.py's
    Api.set_monitors_enabled) pre-compute the exact result-file path so it
    can persist a crash-recovery pending record referencing this same path
    before elevation starts. When omitted the path is generated internally.
    Either way, the final path is validated via `recovery.is_safe_result_path`
    before being interpolated into the elevated helper's command line
    (`_build_helper_params`) -- instance ids are validated against their own
    injection allowlist just above, but a caller-supplied result_path was
    previously interpolated unescaped with no validation at all, mirroring
    the read-side check `read_op_result` already performs and the elevated
    helper's own write-side check in main.py.

    `on_helper_launched`, when supplied, is invoked synchronously with the
    elevated helper's real PID right after launch (via
    `kernel32.GetProcessId` on the raw `ShellExecuteExW` process handle),
    BEFORE waiting for it to finish -- this is what lets a caller persist
    the real helper_pid into its own crash-recovery record as early as
    possible, instead of it staying permanently None (which otherwise
    forces `process_liveness()` to UNKNOWN forever). Omitted by default so
    existing callers that never pass it never touch GetProcessId at all.
    """
    app_dir = app_dir or APP_DIR
    op = "enable" if enabled else "disable"

    if any(not is_valid_instance_id(instance_id) for instance_id in instance_ids):
        return [
            (instance_id, False, "invalid instance id", OUTCOME_GENUINE_FAILURE)
            for instance_id in instance_ids
        ]

    guarded_context = getattr(_guarded_disable_local, "context", None)
    if not enabled and guarded_context is not None:
        return _set_monitors_disabled_with_guard(
            instance_ids,
            app_dir=app_dir,
            timeout_s=timeout_s,
            result_path=result_path,
            on_helper_launched=on_helper_launched,
            context=guarded_context,
        )

    _cleanup_stale_result_files()
    if result_path is None:
        result_path = os.path.join(app_dir, make_result_filename())
    if not recovery.is_safe_result_path(result_path, app_dir):
        raise ValueError(f"Refusing to use unsafe result file path: {result_path!r}")
    params = _build_helper_params(op, instance_ids, result_path)

    handle = _launch_elevated_helper(params)
    if handle is None:
        return [
            (instance_id, False, "Elevation was cancelled or failed", OUTCOME_GENUINE_FAILURE)
            for instance_id in instance_ids
        ]

    if on_helper_launched is not None:
        on_helper_launched(kernel32.GetProcessId(handle))

    completed = _wait_for_helper(handle, timeout_s)
    helper_data = read_op_result(result_path, app_dir) if completed else None

    return _assemble_operation_results(instance_ids, enabled, helper_data, completed)


def _read_guard_result(path: str, app_dir: str):
    """Read and consume only a canonical guard-completion result file."""
    if not recovery.is_safe_guard_result_path(path, app_dir):
        return None
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log_msg(f"Failed to read/parse monitor guard result file {path}: {e}")
        data = None
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
    return data


def _wait_for_guard_initial_result(handle, result_path: str, app_dir: str, timeout_s: float):
    """Wait only until the long-lived helper has reported the disable.

    The process deliberately remains alive after this point.  Its handle is
    still closed here: all later coordination uses the narrow file protocol,
    not a leaked process handle.
    """
    deadline = time.monotonic() + max(0.0, timeout_s)
    try:
        while True:
            data = read_op_result(result_path, app_dir)
            if data is not None:
                return True, data
            if kernel32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0:
                return True, read_op_result(result_path, app_dir)
            if time.monotonic() >= deadline:
                return False, None
            time.sleep(0.05)
    finally:
        kernel32.CloseHandle(handle)


def _remove_if_safe(path: str, app_dir: str, safe_path) -> None:
    if safe_path(path, app_dir):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _set_monitors_disabled_with_guard(
    instance_ids: list,
    *,
    app_dir: str,
    timeout_s: float,
    result_path: str | None,
    on_helper_launched,
    context: _GuardedDisableContext,
) -> list:
    """Start a disable helper that stays elevated only for one guard window."""
    guard_timeout_s = context.timeout_s
    if not 1.0 <= guard_timeout_s <= 60.0:
        raise ValueError("Guard timeout must be between 1 and 60 seconds")

    _cleanup_stale_result_files()
    if result_path is None:
        result_path = os.path.join(app_dir, make_result_filename())
    command_path = os.path.join(app_dir, make_guard_filename("command"))
    completion_path = os.path.join(app_dir, make_guard_filename("result"))
    if not recovery.is_safe_result_path(result_path, app_dir):
        raise ValueError(f"Refusing to use unsafe result file path: {result_path!r}")
    if not recovery.is_safe_guard_command_path(command_path, app_dir):
        raise ValueError(f"Refusing to use unsafe guard command path: {command_path!r}")
    if not recovery.is_safe_guard_result_path(completion_path, app_dir):
        raise ValueError(f"Refusing to use unsafe guard result path: {completion_path!r}")

    _remove_if_safe(command_path, app_dir, recovery.is_safe_guard_command_path)
    _remove_if_safe(completion_path, app_dir, recovery.is_safe_guard_result_path)
    params = _build_guarded_disable_params(
        instance_ids, result_path, command_path, completion_path, guard_timeout_s
    )
    handle = _launch_elevated_helper(params)
    if handle is None:
        return [
            (instance_id, False, "Elevation was cancelled or failed", OUTCOME_GENUINE_FAILURE)
            for instance_id in instance_ids
        ]

    if on_helper_launched is not None:
        on_helper_launched(kernel32.GetProcessId(handle))
    completed, helper_data = _wait_for_guard_initial_result(
        handle, result_path, app_dir, timeout_s
    )
    results = _assemble_operation_results(instance_ids, False, helper_data, completed)
    context.session = GuardedDisableSession(
        instance_ids=tuple(instance_ids),
        command_path=command_path,
        completion_path=completion_path,
        app_dir=app_dir,
    )
    return results


def _wait_for_guard_completion(session: GuardedDisableSession):
    deadline = time.monotonic() + session.completion_timeout_s
    while True:
        data = _read_guard_result(session.completion_path, session.app_dir)
        if data is not None:
            _remove_if_safe(
                session.command_path, session.app_dir, recovery.is_safe_guard_command_path
            )
            return data
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def _signal_guarded_disable(session: GuardedDisableSession, action: str):
    """Request the one allowed terminal action from a guard helper."""
    if action not in {"keep", "revert"}:
        raise ValueError(f"Unknown guard action: {action!r}")
    if session.command_requested:
        return None

    existing = _read_guard_result(session.completion_path, session.app_dir)
    if existing is not None:
        session.command_requested = True
        _remove_if_safe(session.command_path, session.app_dir, recovery.is_safe_guard_command_path)
        return existing
    if not recovery.is_safe_guard_command_path(session.command_path, session.app_dir):
        return None
    if not write_json_atomic(session.command_path, {"action": action}):
        return None
    session.command_requested = True
    return _wait_for_guard_completion(session)


def keep_guarded_disable(session: GuardedDisableSession) -> bool:
    """Close a guard helper without re-enabling its already-disabled ids."""
    completion = _signal_guarded_disable(session, "keep")
    return bool(completion and completion.get("action") == "keep")


def revert_guarded_disable(session: GuardedDisableSession):
    """Re-enable through the same elevated helper, with no second UAC."""
    completion = _signal_guarded_disable(session, "revert")
    if completion is None:
        return None
    return _assemble_operation_results(list(session.instance_ids), True, completion, True)


def read_op_result(path: str, app_dir: str):
    """Read and consume an elevated-helper result file.

    Refuses to open `path` unless `recovery.is_safe_result_path` accepts it.
    On a successful read the file is deleted (it has been consumed); a
    malformed/unreadable file is also removed since the attempt happened,
    an unsafe path is left completely untouched.
    """
    if not recovery.is_safe_result_path(path, app_dir):
        return None
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log_msg(f"Failed to read/parse monitor op result file {path}: {e}")
        data = None
    finally:
        try:
            os.remove(path)
        except Exception:
            pass

    return data


def _get_device_instance_id(hdevinfo, devinfo) -> str:
    required_size = wintypes.DWORD(0)
    setupapi.SetupDiGetDeviceInstanceIdW(
        hdevinfo, ctypes.byref(devinfo), None, 0, ctypes.byref(required_size)
    )
    if required_size.value == 0:
        return ""
    buf = ctypes.create_unicode_buffer(required_size.value)
    if setupapi.SetupDiGetDeviceInstanceIdW(
        hdevinfo, ctypes.byref(devinfo), buf, required_size.value, ctypes.byref(required_size)
    ):
        return buf.value
    return ""


def _get_device_property(hdevinfo, devinfo, prop_id) -> str:
    required_size = wintypes.DWORD(0)
    setupapi.SetupDiGetDeviceRegistryPropertyW(
        hdevinfo, ctypes.byref(devinfo), prop_id, None, None, 0, ctypes.byref(required_size)
    )
    if required_size.value == 0:
        return ""
    buf = ctypes.create_unicode_buffer(required_size.value // 2 + 1)
    if setupapi.SetupDiGetDeviceRegistryPropertyW(
        hdevinfo, ctypes.byref(devinfo), prop_id, None,
        ctypes.byref(buf), required_size.value, ctypes.byref(required_size)
    ):
        return buf.value
    return ""


def _is_devnode_enabled(devinst) -> bool:
    status = wintypes.ULONG(0)
    problem_number = wintypes.ULONG(0)
    result = cfgmgr32.CM_Get_DevNode_Status(
        ctypes.byref(status), ctypes.byref(problem_number), devinst, 0
    )
    if result != CR_SUCCESS:
        # A CfgMgr32 failure here does NOT mean the device is enabled — it
        # means we genuinely don't know. Silently defaulting to "enabled"
        # would be dangerous: crash-recovery (recovery.resolve_pending)
        # trusts device_states as ground truth to decide whether a disable
        # is confirmed, and a wrong "enabled" reading for a monitor that's
        # actually still disabled would erase its recovery flag. Raise
        # instead so callers can fail safe (enumerate_monitors omits the
        # device rather than guessing).
        raise OSError(f"CM_Get_DevNode_Status failed (CONFIGRET error {result})")
    return problem_number.value != CM_PROB_DISABLED


def _locate_devnode(instance_id: str):
    """Injectable seam wrapping CM_Locate_DevNodeW. Returns
    (CONFIGRET result, devinst value) -- callers check `result` against
    CR_SUCCESS before trusting the devinst.
    """
    devinst = wintypes.DWORD(0)
    result = cfgmgr32.CM_Locate_DevNodeW(
        ctypes.byref(devinst), instance_id, CM_LOCATE_DEVNODE_NORMAL
    )
    return result, devinst.value


def _get_devnode_class_guid(devinst) -> str:
    """Raw CM_Get_DevNode_Registry_PropertyW(CM_DRP_CLASSGUID) query --
    drives a live Win32 API, so (like `_is_devnode_enabled`) it is not
    itself unit tested; `_devnode_class_guid` below is the injectable seam
    tests exercise, matching the `_is_devnode_enabled`/`_devnode_enabled`
    pairing above. Returns the class GUID lowercase and without braces
    (e.g. "4d36e96e-e325-11ce-bfc1-08002be10318"), or "" if it can't be
    determined.
    """
    buf = ctypes.create_unicode_buffer(64)
    length = wintypes.ULONG(ctypes.sizeof(buf))
    reg_type = wintypes.ULONG(0)
    result = cfgmgr32.CM_Get_DevNode_Registry_PropertyW(
        devinst, CM_DRP_CLASSGUID, ctypes.byref(reg_type), buf, ctypes.byref(length), 0
    )
    if result != CR_SUCCESS:
        return ""
    return buf.value.strip("{}").lower()


def _devnode_class_guid(devinst) -> str:
    """Injectable seam wrapping `_get_devnode_class_guid`."""
    return _get_devnode_class_guid(devinst)


def _set_devnode_enabled(instance_id: str, enable: bool):
    """Only ever call this from within an already-elevated process.

    Before ever calling CM_Enable_DevNode/CM_Disable_DevNode, verifies the
    resolved devnode actually belongs to GUID_DEVCLASS_MONITOR -- the same
    registry property SetupAPI's class-scoped enumeration
    (_list_raw_monitor_devices, via SetupDiGetClassDevsW's class filter)
    implicitly relies on. This elevated primitive is its own authorization
    boundary and must not rely entirely on its caller (main.py's CLI arg
    parsing, gated only by is_valid_instance_id's injection-safety regex)
    to only ever pass a monitor's instance id -- refuses (raises) instead
    of silently trusting it.
    """
    result, devinst = _locate_devnode(instance_id)
    if result != CR_SUCCESS:
        return False, f"{DEVICE_NOT_FOUND_PREFIX} {instance_id} (CONFIGRET error {result})"

    if _devnode_class_guid(devinst) != GUID_DEVCLASS_MONITOR.lower():
        raise PermissionError(
            f"Refusing to {'enable' if enable else 'disable'} {instance_id}: "
            "not a member of GUID_DEVCLASS_MONITOR"
        )

    if enable:
        result = cfgmgr32.CM_Enable_DevNode(devinst, 0)
    else:
        result = cfgmgr32.CM_Disable_DevNode(devinst, 0)

    if result == CR_SUCCESS:
        return True, f"Monitor {'enabled' if enable else 'disabled'} successfully"
    return False, (
        f"Failed to {'enable' if enable else 'disable'} monitor "
        f"(CONFIGRET error {result})"
    )


def _referenced_result_paths(pending_record) -> set:
    """Every result-file path the given (already-loaded) pending_restore.json
    record still treats as an unconsumed outcome.

    Today's schema (see webview/bridge.py's `_build_and_save_pending_record`)
    only ever stores this once, at the record level -- one elevated-helper
    batch, one shared `result_file` covering every target in that batch.
    Each target dict is checked too, purely defensively, in case a future
    schema starts recording one per target; it costs nothing when absent.
    """
    paths = set()
    if not isinstance(pending_record, dict):
        return paths
    candidates = [pending_record.get("result_file")]
    targets = pending_record.get("targets")
    if isinstance(targets, list):
        for target in targets:
            if isinstance(target, dict):
                candidates.append(target.get("result_file"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            paths.add(os.path.normcase(os.path.abspath(candidate)))
    return paths


def _cleanup_stale_result_files(max_age_s: float = 300.0):
    """Best-effort sweep for orphaned helper IPC files.

    A timed-out set_monitors_enabled() call returns before it can clean
    up its own result_path, because the elevated helper may still be
    running and write it moments later — deleting it immediately would race
    the helper's own write. Each call after that is a good opportunity to
    sweep anything old enough to be a genuine orphan, so these don't
    accumulate in APP_DIR indefinitely.

    Age alone is not sufficient, though: the on-disk crash-recovery record
    (pending_restore.json) can still be pointing at a result file that is
    older than `max_age_s` simply because nothing has consumed it yet (no
    live guard was armed, and the panel/app has not reopened to trigger
    recheck_pending/recover_on_boot). Deleting that file out from under the
    pending record would destroy the elevated helper's confirmed ok/message
    before the recovery ladder ever reads it, degrading what should be a
    clean confirmation or a descriptive failure into a weaker device-state
    -- or liveness-only fallback. Any file still referenced by the current
    pending record is therefore skipped regardless of age; only files with
    no matching reference (or no pending record at all) are removed.
    """
    try:
        referenced = _referenced_result_paths(load_pending())
        now = time.time()
        for name in os.listdir(APP_DIR):
            is_operation_result = (
                name.startswith(recovery.RESULT_FILE_PREFIX)
                and name.endswith(recovery.RESULT_FILE_SUFFIX)
            )
            is_guard_ipc = (
                name.startswith(recovery.GUARD_COMMAND_FILE_PREFIX)
                or name.startswith(recovery.GUARD_RESULT_FILE_PREFIX)
            ) and name.endswith(recovery.GUARD_FILE_SUFFIX)
            if not is_operation_result and not is_guard_ipc:
                continue
            path = os.path.join(APP_DIR, name)
            if is_operation_result and os.path.normcase(os.path.abspath(path)) in referenced:
                continue
            try:
                if now - os.path.getmtime(path) > max_age_s:
                    os.remove(path)
            except Exception:
                pass
    except Exception:
        pass


def run_elevated_worker_op(op: str, instance_id: str):
    return _set_devnode_enabled(instance_id, enable=(op == "enable"))


class PendingDisableGuard:
    """10s auto-revert guard for a confirmed disable -- if the user takes no
    action within the grace period, the disable is automatically undone.

    Pure `remaining_s(now)` / `is_expired(now)` core with an injectable
    `now` -- no real `threading.Timer`/background thread lives inside this
    class, so the countdown is unit-testable without real time passing. The
    caller is responsible for polling `check(now)` (e.g. from a timer tick
    or on panel reopen); on expiry it fires exactly ONE `revert_fn` call
    covering every armed target, never one call per target, and reports the
    true remaining time on reopen rather than a fresh 10 seconds.
    """

    def __init__(self, *, armed_at: float, target_ids: list, revert_fn, timeout_s: float = 10.0):
        self._armed_at = armed_at
        self._target_ids = list(target_ids)
        self._revert_fn = revert_fn
        self._timeout_s = timeout_s
        self._resolved = False
        self._last_results = None

    def remaining_s(self, now: float) -> float:
        return max(0.0, self._timeout_s - (now - self._armed_at))

    def is_expired(self, now: float) -> bool:
        return (now - self._armed_at) >= self._timeout_s

    def confirm(self):
        """Mark this guard as kept/confirmed -- no future `check()` call
        will revert."""
        self._resolved = True

    def check(self, now: float) -> bool:
        """Poll-check the guard. If expired and not yet resolved (neither
        confirmed nor already reverted), fires the single revert call for
        every armed target. Returns True whenever this call actually
        attempted a revert (i.e. it was expired and unresolved) --
        regardless of whether that attempt genuinely succeeded. Callers
        MUST NOT treat a True return as proof of success; read
        `last_results`/`resolved` for the real outcome.

        `resolved` is set only AFTER `revert_fn`
        returns, not before. `revert_fn` is a real Win32/ctypes call chain
        in production and can raise; if it does, this method lets the
        exception propagate (the caller -- bridge.py's
        `_resolve_guard_unbounded_under_lock`, via `config.call_logged` -- is what turns
        it into a logged, non-fatal failure) and deliberately leaves the
        guard unresolved, so a subsequent `check()` call retries the revert
        instead of silently treating a failed revert as done.

        "Didn't raise" is not the same as
        "genuinely succeeded". The real production `revert_fn` (bridge.py's
        lambda wrapping `monitors.set_monitors_enabled`) is deliberately
        built to NEVER raise for expected failure modes -- it always
        returns a `list[(instance_id, ok, message, kind)]` instead, exactly like
        `set_monitors_enabled`'s own return shape, with `ok=False` entries
        for a genuine per-target failure. A prior version of this method
        marked itself `resolved=True` the moment `revert_fn` returned at
        all, so a revert that failed for every single target (an entirely
        normal, non-exceptional outcome for that function) was still
        reported as "resolved" -- letting a caller clear the on-disk
        crash-recovery record for a monitor that, in truth, is still
        disabled.

        `revert_fn`'s return value is captured on `last_results` every time
        it is actually called (success or partial failure alike), and
        `resolved` only flips to True once EVERY target id in
        `self._target_ids` has a genuinely successful (`ok=True`) result.
        A partial success (some targets revert, others don't) leaves the
        guard unresolved, so a later poll retries the whole batch again
        (re-reverting an already-enabled target is a harmless no-op). This
        lets callers (bridge.py's `_resolve_guard_unbounded_under_lock`) trim only the
        genuinely-succeeded targets out of the crash-recovery record instead
        of assuming the whole batch resolved just because nothing raised.
        """
        if self._resolved:
            return False
        if not self.is_expired(now):
            return False
        results = self._revert_fn(self._target_ids)
        self._last_results = results
        succeeded_ids = {instance_id for instance_id, ok, _, _ in results if ok}
        if succeeded_ids == set(self._target_ids):
            self._resolved = True
        return True

    def rearm(self, now: float, delay_s: float) -> None:
        """Reschedule this guard's own expiry window to `delay_s` seconds
        from `now` -- the same real gap bridge.py's `_arm_guard_timer`
        re-arms its `threading.Timer` for, whether that is the initial 10s
        grace period or a later bounded backoff retry
        (`_maybe_retry_auto_revert`'s `_AUTO_REVERT_RETRY_DELAY_S`).

        Before this existed, `is_expired`/`check` compared `now` only
        against `armed_at`/`timeout_s` as captured ONCE at `__init__` --
        so once a guard passed its first deadline, `is_expired` stayed
        permanently True for the rest of its life, no matter how far in
        the future the real next scheduled retry actually was. Any caller
        that resolves the guard between two scheduled attempts (a
        `_resolve_guard_unbounded_under_lock` call with `source_timer=
        None`, e.g. webview/app.py's window-close handler or
        `confirm_update`'s background resolver -- neither is tied to a
        specific timer instance, so neither is caught by the stale-timer
        guard) would immediately re-fire a real revert attempt ahead of
        schedule instead of waiting for the timer that is actually still
        pending. Calling `rearm` in lockstep with every real timer
        (re-)arm keeps `is_expired`'s notion of "expired" matching "the
        next legitimate attempt is actually due", not just "has ever been
        due once".

        A no-op once the guard is already resolved (confirmed, fully
        reverted, or emptied via `remove_targets`) -- a stray late rearm
        racing a call that just resolved the guard must not reopen or
        reschedule an already-frozen guard.
        """
        if self._resolved:
            return
        self._armed_at = now
        self._timeout_s = delay_s

    def remove_targets(self, target_ids):
        """Partial-target resolution: drops `target_ids` from this guard's
        tracked set WITHOUT calling `revert_fn` or fully resolving the
        guard. Used when only SOME of a batch's targets get manually
        re-enabled before the auto-revert timer fires (bridge.py's
        `_resolve_guard_for_enabled_ids`) -- the guard/timer stay armed and
        will still protect whatever targets remain when it later expires or
        is next polled, instead of the whole guard being torn down on any
        partial overlap.

        `resolved` only flips to True once the tracked target set becomes
        empty (every target has been accounted for, either reverted via
        `check()` or removed here) -- the same "every target covered"
        condition `check()` itself uses. A no-op once the guard is already
        resolved, so a stray late call can't reopen or mutate a frozen
        guard.
        """
        if self._resolved:
            return
        dropped = set(target_ids)
        self._target_ids = [t for t in self._target_ids if t not in dropped]
        if not self._target_ids:
            self._resolved = True

    @property
    def target_ids(self):
        return list(self._target_ids)

    @property
    def timeout_s(self):
        return self._timeout_s

    @property
    def resolved(self):
        return self._resolved

    @property
    def last_results(self):
        """The `list[(instance_id, ok, message, kind)]` from the most recent
        `check()` call that actually invoked `revert_fn`, or `None` if no
        such call has happened yet (never expired, or the only attempt so
        far raised before this could be set)."""
        return list(self._last_results) if self._last_results is not None else None

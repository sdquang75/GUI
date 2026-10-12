import ctypes
import ctypes.wintypes
import json
import msvcrt
import os
import sys
import threading
import time
import traceback
import winreg

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32

ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
MUTEX_NAME = "QuickRes_SingleInstance_Mutex"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def _is_reparse_point(path: str) -> bool:
    """Injectable seam: True if `path` is an NTFS reparse point (directory
    junction, symlink, mount point, ...) per GetFileAttributesW. Returns
    False if the attributes call itself fails (path missing/inaccessible)
    -- that is a different failure mode, already handled by get_app_dir()'s
    own makedirs/try-except, not by this check.

    GetFileAttributesW's ctypes binding defaults to a signed return type
    (c_long), while the Win32 API documents its failure sentinel as the
    unsigned value 0xFFFFFFFF. Left unmasked, a failed call comes back as
    -1 in Python, which never equals the unsigned INVALID_FILE_ATTRIBUTES
    constant -- so the failure branch below would never trigger, and -1's
    all-ones bit pattern would make the subsequent bitwise AND report every
    missing/inaccessible path as a reparse point. Masking to 32 bits first
    recovers the unsigned value the API actually documents."""
    attrs = kernel32.GetFileAttributesW(path) & 0xFFFFFFFF
    if attrs == INVALID_FILE_ATTRIBUTES:
        return False
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


GENERIC_WRITE = 0x40000000
CREATE_ALWAYS = 2
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _ByHandleFileInformation(ctypes.Structure):
    """Win32 BY_HANDLE_FILE_INFORMATION. Only dwFileAttributes is ever read
    by _open_no_reparse_follow() below -- the remaining fields exist only so
    this struct's size/layout matches what GetFileInformationByHandle
    expects to write into."""

    _fields_ = [
        ("dwFileAttributes", ctypes.wintypes.DWORD),
        ("ftCreationTime", ctypes.wintypes.FILETIME),
        ("ftLastAccessTime", ctypes.wintypes.FILETIME),
        ("ftLastWriteTime", ctypes.wintypes.FILETIME),
        ("dwVolumeSerialNumber", ctypes.wintypes.DWORD),
        ("nFileSizeHigh", ctypes.wintypes.DWORD),
        ("nFileSizeLow", ctypes.wintypes.DWORD),
        ("nNumberOfLinks", ctypes.wintypes.DWORD),
        ("nFileIndexHigh", ctypes.wintypes.DWORD),
        ("nFileIndexLow", ctypes.wintypes.DWORD),
    ]


# Explicit argtypes/restype are required here: without them ctypes defaults
# a foreign function's return type to a 32-bit signed int, which silently
# truncates/misreads the 64-bit handle CreateFileW actually returns on a
# 64-bit process (INVALID_HANDLE_VALUE is all-64-bits-set, not just the
# low 32 bits). GetFileAttributesW elsewhere in this file has the same
# signed/unsigned pitfall on its 32-bit DWORD return, worked around instead
# by masking the result -- see _is_reparse_point()'s docstring.
kernel32.CreateFileW.argtypes = [
    ctypes.wintypes.LPCWSTR,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.LPVOID,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.HANDLE,
]
kernel32.CreateFileW.restype = ctypes.wintypes.HANDLE
kernel32.GetFileInformationByHandle.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.POINTER(_ByHandleFileInformation),
]
kernel32.GetFileInformationByHandle.restype = ctypes.wintypes.BOOL
kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
# Same pitfall as CreateFileW above, for a different reason: LocalFree's
# lone argument is a pointer, and ctypes marshals an untyped plain Python
# int argument as a 32-bit C int/long by default (Windows' LLP64 model
# keeps `long` 32-bit even in a 64-bit process). LocalAlloc-backed pointers
# (e.g. the security descriptor ConvertStringSecurityDescriptorToSecurityDescriptorW
# allocates in _build_owner_only_mutex_security()) routinely land above the
# 32-bit range on 64-bit Windows, so an untyped call raises
# "OverflowError: int too long to convert" -- this crashed every real launch
# via _create_or_open_mutex() despite the full test suite passing, because
# the only test exercising this exact call site worked around the missing
# argtypes locally instead of fixing them here.
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
kernel32.LocalFree.restype = ctypes.c_void_p


def _open_no_reparse_follow(path: str, binary: bool = False, append: bool = False):
    """Injectable seam: create-or-truncate-and-open `path` for writing, the
    same way `open(path, "w")` would, except that a reparse point (NTFS
    symlink/junction) sitting at `path` is opened as itself instead of being
    transparently followed through to whatever it points at.

    `binary`, when True, hands back a bytes-mode file object (equivalent to
    `open(path, "wb")`) instead of the default text/UTF-8 mode -- both modes
    share the exact same reparse-refusing CreateFileW call below, this only
    changes how the already-opened handle is wrapped. Added so a caller
    writing binary content (e.g. quickres/updater.py staging a downloaded
    executable) can reuse this same guard instead of plain open(), without
    disturbing the default text-mode behavior every existing caller of this
    function already relies on.

    `append`, when True, hands back a file object equivalent to
    `open(path, "a", encoding="utf-8")` instead: the file is opened with
    OPEN_ALWAYS (create it if missing, but never truncate an existing one)
    rather than CREATE_ALWAYS, and the handle's position is seeked to the
    current end-of-file before being wrapped, so writes land after existing
    content instead of overwriting it. Added for quickres/config.py's own
    log_msg(), which must accumulate log lines across repeated calls rather
    than truncating quickres.log on every single write -- CREATE_ALWAYS's
    always-truncate semantics (what every other caller of this function
    wants) would otherwise destroy prior log history each time. Both modes
    share the exact same reparse-refusing CreateFileW/
    GetFileInformationByHandle sequence below; this only changes the
    creation disposition passed to CreateFileW and how the already-opened
    handle is wrapped.

    Plain open() (and os.replace()) ultimately call CreateFileW without
    FILE_FLAG_OPEN_REPARSE_POINT. On Windows that makes CreateFileW follow a
    file symlink to its target, and with CREATE_ALWAYS semantics the target
    is truncated the instant the call succeeds -- before any Python-level
    code has a chance to notice and refuse. A "check reparse status, then
    open" sequence, no matter how tight the two calls sit next to each
    other, always leaves some window between the check and the open in
    which a symlink can be planted, and the truncation this function exists
    to prevent has already happened by the time such a check could run.
    Passing FILE_FLAG_OPEN_REPARSE_POINT removes that window entirely: the
    single CreateFileW call itself opens the reparse point object, never a
    followed target, so nothing downstream of it is ever truncated --
    whether the object turns out to be a reparse point is then just a
    property of the handle already in hand, not a separate, racy filesystem
    check performed against the path a second time.

    Returns a writable Python file object (equivalent to what
    `open(path, "w", encoding="utf-8")` would hand back) when `path` names a
    genuine new or existing regular file -- created/truncated exactly as
    open() would, just without ever risking a followed-target truncation
    along the way. Returns None, with the handle already closed and nothing
    written or truncated, when `path` is itself a reparse point.
    """
    handle = kernel32.CreateFileW(
        path,
        GENERIC_WRITE,
        0,
        None,
        OPEN_ALWAYS if append else CREATE_ALWAYS,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise OSError(f"CreateFileW failed for {path} (GetLastError={kernel32.GetLastError()})")

    info = _ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        last_error = kernel32.GetLastError()
        kernel32.CloseHandle(handle)
        raise OSError(f"GetFileInformationByHandle failed for {path} (GetLastError={last_error})")

    if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
        kernel32.CloseHandle(handle)
        return None

    fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
    if append:
        # OPEN_ALWAYS leaves the handle's file position at 0 even when the
        # file already existed -- unlike plain open(path, "a"), which always
        # starts at end-of-file. Seek explicitly so the first write here
        # lands after existing content instead of overwriting its start.
        os.lseek(fd, 0, os.SEEK_END)
        return os.fdopen(fd, "ab") if binary else os.fdopen(fd, "a", encoding="utf-8")
    if binary:
        return os.fdopen(fd, "wb")
    return os.fdopen(fd, "w", encoding="utf-8")


def get_app_dir() -> str:
    """Return the directory QuickRes stores config.json/quickres.log/
    pending_restore.json in, creating %LOCALAPPDATA%\\QuickRes on first run
    if necessary.

    FILESYSTEM SIDE EFFECT: `APP_DIR = get_app_dir()` below runs at module
    IMPORT time, so simply `import quickres.config` creates this directory
    (via os.makedirs) as a side effect, before any application code runs.

    This directory-junction check guards against a local elevation-of-
    privilege attack: a standard-privilege process needs no special
    rights to plant an NTFS directory junction/reparse point at this exact
    path, either before QuickRes's first run or any time after the folder
    is deleted. If that were trusted silently, the ELEVATED helper process
    (main.py's _run_elevated_helper, launched via ShellExecuteExW "runas")
    would later write config/pending-restore files through this path --
    Windows I/O transparently follows a junction, so the elevated write
    would land wherever the attacker pointed it, under cover of the single
    UAC prompt the user approved for something else entirely. There is no
    legitimate reason a freshly-created (or exist_ok=True no-op'd) app-data
    directory should already be a reparse point, so refuse it outright:
    _is_reparse_point() is checked right after os.makedirs() succeeds, and
    a positive result raises -- which the existing except-block below
    catches, falling back to the (benign, non-elevated) exe-directory path
    instead of ever returning/trusting the junctioned directory.
    """
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            path = os.path.join(local_app_data, "QuickRes")
            try:
                os.makedirs(path, exist_ok=True)
                if _is_reparse_point(path):
                    raise OSError(f"{path} is a reparse point/junction; refusing to trust it")
                return path
            except Exception:
                pass

    base_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    return base_dir


# Importing this module has a filesystem side effect: get_app_dir() may
# create %LOCALAPPDATA%\QuickRes via os.makedirs() (see its docstring).
APP_DIR = get_app_dir()
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "quickres.log")
PENDING_PATH = os.path.join(APP_DIR, "pending_restore.json")


def detect_system_theme() -> str:
    """Read the Windows 10/11 "Apps use light/dark mode" setting. Only used
    as the fallback default when the user has never picked a theme in the
    app -- once a theme is explicitly saved to config, it always wins over
    the OS setting on later launches.
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if value else "dark"
    except OSError:
        return "dark"


# quickres.log is the only observability channel for a `console=False`
# packaged build, and nothing caps its size on its own -- a long-running
# install could grow it unbounded. 3 MB comfortably holds a lot of history
# while staying cheap to
# read/attach; once a call would push the file past this, the whole file is
# rotated out to a single quickres.log.old backup (overwriting any prior
# backup) rather than truncated in place, so at most ~2x LOG_MAX_BYTES of
# log data ever sits on disk and nothing is silently deleted mid-session.
LOG_MAX_BYTES = 3 * 1024 * 1024


def _rotate_log_if_needed():
    """Injectable seam: if LOG_PATH already meets/exceeds LOG_MAX_BYTES,
    move it to LOG_PATH + '.old' (clobbering any previous backup) so the
    next append starts a fresh file. os.path.getsize() is a single stat
    call -- cheap enough to run on every log_msg() invocation. Missing file
    (first-ever log, or right after a prior rotation) and any other OSError
    are both treated as "nothing to rotate", not an error -- log_msg's own
    caller must never see an exception from logging itself.

    A same-user, unprivileged attacker able to plant an NTFS reparse point
    (symlink/junction) at LOG_PATH could otherwise have this function stat
    and rename through it -- consistent with how every other on-disk write
    in this module (write_json_atomic, get_app_dir) refuses to trust a path
    that is itself a reparse point, LOG_PATH is checked first and rotation
    is skipped entirely (treated the same as "nothing to rotate") when it
    is one. log_msg()'s own _open_no_reparse_follow(..., append=True) call
    is the actual write-time guard against a followed-target write; this
    check only prevents this function's stat/rename from touching a
    reparse point at all.

    Callers must hold `_log_lock` while calling this -- see log_msg().
    """
    try:
        if _is_reparse_point(LOG_PATH):
            return
        if os.path.getsize(LOG_PATH) >= LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".old")
    except OSError:
        pass


# log_msg() is, like update_config() (see _update_lock above) and
# write_json_atomic()'s thread-id-suffixed tmp names, called from whatever
# thread pywebview happens to dispatch a given JS->Python bridge call on --
# so two threads can call log_msg() at the same moment. Without a lock
# around the whole rotate-check-then-append sequence, one thread's
# os.replace(LOG_PATH, LOG_PATH + '.old') could fire while another thread
# still holds an open append handle to the old inode, and a later rotation
# could then silently discard that thread's write -- contradicting
# LOG_MAX_BYTES's own docstring guarantee that nothing is silently deleted
# mid-session. Two unsynchronized `open(path, 'a')` handles writing at the
# same instant can also interleave partial lines. Serializing the entire
# sequence closes both gaps.
_log_lock = threading.Lock()


def log_msg(msg: str):
    # log_msg is reachable from the ELEVATED helper process (main.py's
    # _run_elevated_helper, running with the admin token from
    # ShellExecuteExW "runas"): write_json_atomic()'s own except-block calls
    # log_msg() on any I/O failure, and that call executes inside the same
    # elevated process. LOG_PATH is therefore opened through
    # _open_no_reparse_follow(..., append=True) -- the same reparse-refusing
    # CreateFileW guard write_json_atomic() and updater.py already use --
    # instead of plain open(), which would transparently follow a
    # pre-planted NTFS symlink at LOG_PATH and append attacker-triggered
    # content at the symlink's target with the elevated process's own
    # privileges.
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{timestamp} {msg}\n"
    try:
        with _log_lock:
            _rotate_log_if_needed()
            f = _open_no_reparse_follow(LOG_PATH, append=True)
            if f is None:
                return
            with f:
                f.write(line)
    except Exception:
        pass


def call_logged(fn, *args, on_error: str = "", **kwargs):
    """Call `fn(*args, **kwargs)`, logging any raised exception via
    `log_msg` before swallowing it, instead of letting it vanish with zero
    trace. Meant for background/teardown callbacks that have no caller
    waiting to see a raised exception (a `threading.Timer` firing in the
    background, a window-close handler running during process teardown) --
    unlike `webview/bridge.py`'s `bridge_op`, which only ever wraps a
    JS-invoked `Api` method, nothing wraps these, and QuickRes.spec builds
    with `console=False`, so a silent exception here is otherwise
    completely unobservable. Lives here (not in webview/bridge.py) so
    bridge.py's own enforced invariant of exactly one `try:` statement
    (the one inside its `bridge_op` decorator) stays intact while its
    timer/close-handler callbacks still get the same log-on-failure
    treatment. Returns `fn`'s return value, or `None` if it raised.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        label = on_error or getattr(fn, "__name__", "callback")
        log_msg(f"{label} failed: {exc!r}\n{traceback.format_exc()}")
        return None


def write_json_atomic(path: str, data: dict) -> bool:
    # pywebview dispatches every JS->Python bridge call on its own new
    # thread, and several bridge_op methods that write config carry no lock
    # over the write -- so a pid-only suffix let two concurrent writers (on
    # different threads, same pid) collide on the same temp file. Folding in
    # threading.get_ident() makes the name unique per invocation, not just
    # per process.
    tmp_path = path + f".tmp{os.getpid()}.{threading.get_ident()}"

    # This is a TOCTOU guard: get_app_dir()'s reparse-point/junction check
    # runs exactly once, at module-import time,
    # which is not close enough in time to the privileged write this
    # protection exists for -- a same-user, unprivileged process (full
    # permissions on its own %LOCALAPPDATA%\QuickRes) has a window to
    # delete and re-plant the directory as a junction between that one-time
    # check and this call, and an elevated caller's write would then
    # transparently follow it. Re-verify the target's containing directory
    # right here, immediately before every write, independent of whatever
    # get_app_dir() concluded earlier -- this is IN ADDITION to that
    # one-time check (still useful as an early sanity check), not a
    # replacement for it.
    target_dir = os.path.dirname(path)
    if target_dir and _is_reparse_point(target_dir):
        log_msg(f"Refusing to write {path}: {target_dir} is a reparse point/junction")
        return False

    # tmp_path's name is fully predictable from this process's own pid and
    # (main) thread id, both learnable by another same-user process watching
    # for QuickRes to start. A standard user with SeCreateSymbolicLinkPrivilege
    # (available under Windows Developer Mode) can pre-create a file-level
    # NTFS symlink at that exact path before this call runs. A separate
    # "check whether tmp_path is a reparse point, then open it" sequence is
    # never enough on its own: Windows' CreateFileW -- which plain open()
    # ultimately calls -- transparently follows a file symlink, and with
    # CREATE_ALWAYS semantics truncates whatever it points at the instant
    # the call succeeds, before any check performed after that point could
    # ever run. tmp_path is therefore opened through _open_no_reparse_follow()
    # instead of the builtin open() -- a single atomic Win32 call that opens
    # a reparse point as itself rather than a followed target, so the
    # truncation this guards against is never possible in the first place,
    # not merely caught after the fact. See that function's own docstring
    # for the full mechanism.
    try:
        f = _open_no_reparse_follow(tmp_path)
        if f is None:
            raise OSError(f"{tmp_path} is a reparse point/symlink; refusing to write through it")
        with f:
            json.dump(data, f)
        # target_dir can still be turned into a junction in the window
        # between the pre-open check above and this rename, independent of
        # whatever happened to tmp_path itself -- rechecking immediately
        # before the commit closes that separate race.
        if target_dir and _is_reparse_point(target_dir):
            raise OSError(f"{target_dir} is a reparse point/junction (detected immediately before replace)")
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        log_msg(f"Failed to write {path}: {e}")
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return False


def load_config() -> dict:
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log_msg(f"Failed to read config.json: {e}")
    return {}


def save_config(config_dict: dict) -> bool:
    return write_json_atomic(CONFIG_PATH, config_dict)


_update_lock = threading.Lock()


def update_config(updates: dict):
    # pywebview dispatches every JS->Python bridge call on its own new
    # thread, and several bridge_op methods (set_theme, set_language,
    # start_hotkey, ...) call this without lock=True. Without serializing
    # the full read-modify-write sequence, two concurrent callers can both
    # load the same stale config and each save their own single-key update,
    # silently discarding one another's change (lost update). This doesn't
    # need per-key locking -- just serialize the whole sequence against
    # itself.
    with _update_lock:
        cfg = load_config()
        cfg.update(updates)
        # save_config()'s bool return must not be discarded here: a silently
        # failed write_json_atomic (disk full, permissions, AV lock, ...)
        # would otherwise be reported as success to every caller --
        # set_theme/set_language/start_hotkey (bridge.py) would all return
        # {"ok": True} even though nothing was actually persisted. Raising
        # directly (rather than changing this function's
        # return shape to a bool) needs no call-site changes: bridge.py's
        # bridge_op decorator already turns any raised exception into an
        # {"ok": False, "kind": "error", ...} envelope.
        if not save_config(cfg):
            raise RuntimeError(f"Failed to write config.json (updates={updates!r})")
        return cfg


def save_pending(record: dict) -> bool:
    return write_json_atomic(PENDING_PATH, record)


def load_pending() -> dict | None:
    try:
        if os.path.exists(PENDING_PATH):
            with open(PENDING_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log_msg(f"Failed to read pending_restore.json: {e}")
    return None


def clear_pending() -> None:
    try:
        if os.path.exists(PENDING_PATH):
            os.remove(PENDING_PATH)
    except Exception as e:
        log_msg(f"Failed to clear pending_restore.json: {e}")


def pending_mtime() -> float | None:
    try:
        return os.path.getmtime(PENDING_PATH)
    except OSError:
        return None


_instance_mutex = None


SDDL_REVISION_1 = 1

# Allow Generic-All to Owner only. No explicit Deny-Everyone ACE: see the
# _build_owner_only_mutex_security() docstring for why an earlier revision's
# "D:(D;;GA;;;WD)(A;;GA;;;OW)" (Deny-Everyone listed before Allow-Owner)
# denied the object's own owner too.
_MUTEX_SDDL = "D:(A;;GA;;;OW)"


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", ctypes.c_ulong),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int),
    ]


def _build_owner_only_mutex_security():
    """Injectable seam: best-effort construction of a SECURITY_ATTRIBUTES
    struct whose DACL grants full access to MUTEX_NAME only to the current
    user and explicitly denies it to everyone else ("Everyone"/World).
    Without this, CreateMutexW(None, ...) creates the mutex with default
    security, which on a typical desktop grants any other same-user,
    unprivileged process enough access to pre-create/open the exact same
    named mutex before QuickRes ever runs -- letting it make
    enforce_single_instance() always believe the real app is already
    running, so the real app exits without ever creating its window (a
    same-user denial-of-service).

    Builds the descriptor from the SDDL string _MUTEX_SDDL ("D:(A;;GA;;;OW)"
    -- allow Generic-All to Owner only) via
    ConvertStringSecurityDescriptorToSecurityDescriptorW rather than
    hand-assembling SID/ACL/ACE structures with ctypes: it is the
    Microsoft-documented shortcut for exactly this "owner-only" case and
    far less error-prone to get right.

    This deliberately has no explicit Deny-Everyone ACE. An earlier
    revision used "D:(D;;GA;;;WD)(A;;GA;;;OW)" (deny Generic-All to World,
    *then* allow Generic-All to Owner), but Windows AccessCheck walks a
    DACL's ACEs in listed order and denies the whole request at the first
    matching Deny ACE -- it never skips ahead to a later, more specific
    Allow ACE for the same principal. Every logged-on user's token
    (including the object's own owner) carries the Everyone/World SID, so
    that Deny(WD) ACE also matched the owner and denied it, before the
    Allow(OW) ACE was ever reached: the owner could create the mutex on
    first launch (object creation is not access-checked against its own
    new DACL) but could never reopen it on a second launch, which instead
    failed with ERROR_ACCESS_DENIED. A DACL containing only an explicit
    Allow-Owner ACE already implicitly denies every other, unlisted
    principal under normal Windows semantics, so dropping the redundant
    (and self-defeating) Deny-Everyone ACE loses no hardening.

    Returns None on any failure. CreateMutexW's lpMutexAttributes parameter
    already accepts NULL to mean "use default security" -- the original,
    unhardened behavior -- so a failure here is treated as "hardening
    unavailable this run", not a reason to abort single-instance
    enforcement altogether.
    """
    try:
        sd_ptr = ctypes.c_void_p()
        ok = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            _MUTEX_SDDL, SDDL_REVISION_1, ctypes.byref(sd_ptr), None
        )
        if not ok or not sd_ptr:
            return None
        sa = _SecurityAttributes()
        sa.nLength = ctypes.sizeof(_SecurityAttributes)
        sa.lpSecurityDescriptor = sd_ptr
        sa.bInheritHandle = False
        return sa
    except Exception:
        return None


def _create_or_open_mutex():
    """Injectable seam around CreateMutexW + GetLastError. Returns
    (mutex_handle, already_running: bool).

    Created with an owner-only DACL (_build_owner_only_mutex_security())
    rather than CreateMutexW's default security, so another same-user
    process cannot pre-create/squat MUTEX_NAME before QuickRes runs to
    force this guard into always believing the real app is already
    running.

    A falsy (NULL) handle used to be silently folded into "this is the
    first instance" no matter why CreateMutexW failed -- but NULL only
    ever legitimately means "another instance is running" when it comes
    with GetLastError() == ERROR_ALREADY_EXISTS. Any other failure (for
    example a pre-existing, differently-typed kernel object already
    squatting this exact name, which surfaces as ERROR_INVALID_HANDLE) is
    an unexpected condition the single-instance guard was never designed
    to interpret as "safe to proceed" -- so it is logged here (quickres.log
    is the only observability channel available in this console=False
    build) and, consistent with how this file already fails closed
    elsewhere (_is_reparse_point()/write_json_atomic() refuse to trust an
    ambiguous filesystem state rather than proceed), treated the same as
    already_running=True: refusing to start a second instance because the
    guard itself broke is safer than silently allowing a duplicate.
    """
    sa = _build_owner_only_mutex_security()
    mutex = kernel32.CreateMutexW(ctypes.byref(sa) if sa is not None else None, False, MUTEX_NAME)
    # GetLastError() must be read immediately after CreateMutexW, before any
    # other Win32 call (including the LocalFree cleanup below) has a chance
    # to overwrite the calling thread's last-error value.
    last_error = kernel32.GetLastError()
    if sa is not None:
        # The security descriptor is only consulted by CreateMutexW at
        # creation time -- it is copied into the kernel object, not kept
        # referenced -- so the LocalAlloc'd buffer
        # ConvertStringSecurityDescriptorToSecurityDescriptorW allocated
        # for it can be released immediately afterward.
        kernel32.LocalFree(sa.lpSecurityDescriptor)
    if not mutex:
        if last_error != ERROR_ALREADY_EXISTS:
            log_msg(
                f"CreateMutexW({MUTEX_NAME!r}) returned no handle and an "
                f"unexpected GetLastError()={last_error} (not "
                f"ERROR_ALREADY_EXISTS={ERROR_ALREADY_EXISTS}); treating this "
                f"as if another instance is already running rather than "
                f"risking a duplicate instance."
            )
        return mutex, True
    already_running = last_error == ERROR_ALREADY_EXISTS
    return mutex, already_running


FOREGROUND_RETRY_TOTAL_S = 2.0
FOREGROUND_RETRY_INTERVAL_S = 0.15


def _get_own_exe_path() -> str:
    """Injectable seam: this process's own running executable path, used to
    verify a candidate "QuickRes" window actually belongs to this same
    application before _find_and_foreground_attempt() trusts it enough to
    steal focus. sys.executable is QuickRes.exe in a frozen build, or the
    python.exe interpreter in dev mode -- either way it is the actual
    executable this process was launched from, the same distinction
    get_app_dir() already draws elsewhere in this file."""
    return os.path.normcase(os.path.abspath(sys.executable))


def _get_window_owner_pid(hwnd) -> int:
    """Injectable seam around GetWindowThreadProcessId: returns the pid of
    the process that owns `hwnd`, or 0 if the lookup fails."""
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _get_process_exe_path(pid: int) -> str | None:
    """Injectable seam: best-effort lookup of the full executable path for
    an arbitrary process id, via OpenProcess + QueryFullProcessImageNameW.
    Returns None on any failure (pid is 0/invalid, the process has already
    exited, access is denied, ...) -- callers must treat None the same as
    "path does not match this process's own executable", never as a
    match."""
    if not pid:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = ctypes.c_ulong(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return os.path.normcase(buf.value)
    finally:
        kernel32.CloseHandle(handle)


def _find_and_foreground_attempt() -> bool:
    """Injectable seam: a single attempt to find and foreground the
    already-running QuickRes window. Returns True if the window was found
    (and foregrounded), False if not found yet.

    FindWindowW(None, "QuickRes") only ever matches on window title, which
    is not an authenticated identifier -- any same-user, unprivileged
    process can create a window with that exact title with no special
    rights, and unconditionally trusting whatever hwnd comes back would let
    such a process steal focus on every QuickRes launch. Before
    foregrounding the found window, its owning process's own executable
    path is looked up (GetWindowThreadProcessId, then
    OpenProcess/QueryFullProcessImageNameW) and compared against this
    process's own executable path (_get_own_exe_path()) -- only an exact
    match is trusted. A mismatch, or a failed owner-path lookup, is treated
    the same as "no window found yet" so the caller's existing retry loop
    keeps waiting for the real window instead of ever foregrounding an
    impostor.
    """
    hwnd = user32.FindWindowW(None, "QuickRes")
    if not hwnd:
        return False
    owner_pid = _get_window_owner_pid(hwnd)
    owner_path = _get_process_exe_path(owner_pid)
    own_path = _get_own_exe_path()
    if owner_path is None or owner_path != own_path:
        log_msg(
            f"Ignoring a window titled 'QuickRes' (hwnd={hwnd}, owning "
            f"pid={owner_pid}) whose executable ({owner_path!r}) does not "
            f"match this process's own executable ({own_path!r}) -- not "
            f"foregrounding it."
        )
        return False
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True


def _foreground_existing_window():
    """enforce_single_instance() creates the named mutex before the
    pywebview window exists, so a second launch racing that brief startup
    gap could get exactly one immediate _find_and_foreground_attempt() that
    found nothing yet (the first instance's window hadn't been created),
    then silently exit with zero visible feedback. Poll for a short bounded
    grace period instead --
    a genuinely-already-running instance (window already exists) still
    resolves near-instantly since the first attempt succeeds and the loop
    returns immediately, while a genuinely-not-running scenario still gives
    up quickly (FOREGROUND_RETRY_TOTAL_S caps the total wait), but a second
    launch during the startup window now keeps retrying until the first
    instance's window actually appears."""
    deadline = time.monotonic() + FOREGROUND_RETRY_TOTAL_S
    while True:
        if _find_and_foreground_attempt():
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(FOREGROUND_RETRY_INTERVAL_S)


def enforce_single_instance() -> bool:
    """Named-mutex single-instance guard.

    Returns True when this is the first/only instance -- the caller should
    proceed to create its window. Returns False when another instance is
    already running -- the existing window has already been foregrounded by
    this call, and the caller MUST exit without creating a new window
    (this function itself no longer calls sys.exit -- that decision belongs
    to the caller, since a headless caller like the elevated helper branch
    should never reach this function at all).
    """
    global _instance_mutex
    mutex, already_running = _create_or_open_mutex()
    if already_running:
        _foreground_existing_window()
        return False
    _instance_mutex = mutex
    return True


def resource_path(relative_path):
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(getattr(sys, "_MEIPASS", ""), relative_path))
        candidates.append(os.path.join(os.path.dirname(sys.executable), relative_path))
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(project_root, relative_path))

    for path in candidates:
        if os.path.exists(path):
            return path

    return candidates[0]
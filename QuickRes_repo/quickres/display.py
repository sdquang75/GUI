import ctypes
import math
import os
import subprocess
import webbrowser

from quickres.config import log_msg

user32 = ctypes.windll.user32


class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_ulong),
        ("dmPositionX", ctypes.c_long),
        ("dmPositionY", ctypes.c_long),
        ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong),
        ("dmPelsHeight", ctypes.c_ulong),
        ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong),
    ]


DM_PELSWIDTH = 0x80000
DM_PELSHEIGHT = 0x100000
DM_DISPLAYFREQUENCY = 0x400000

CDS_UPDATEREGISTRY = 0x00000001
CDS_NORESET = 0x10000000
CDS_RESET = 0x40000000

ENUM_CURRENT_SETTINGS = -1

QUICK_LIST = [
    ("1920 x 1080", 1920, 1080),
    ("2560 x 1440", 2560, 1440),
    ("1920 x 1440", 1920, 1440),
    ("1620 x 1080", 1620, 1080),
    ("1568 x 1080", 1568, 1080),
    ("1440 x 1080", 1440, 1080),
    ("1600 x 1080", 1600, 1080),
    ("1280 x 1080", 1280, 1080),
    ("1350 x 1080", 1350, 1080),
    ("1280 x 1024", 1280, 1024),
    ("1080 x 1080", 1080, 1080),
    ("1280 x 960", 1280, 960),
    ("1024 x 768", 1024, 768),
    ("800 x 1080", 800, 1080),
]


def aspect_ratio_label(width: int, height: int) -> str:
    """GCD-reduced aspect-ratio label, e.g. (1920, 1080) -> "16:9"."""
    divisor = math.gcd(width, height) or 1
    return f"{width // divisor}:{height // divisor}"


def classify_resolution(width: int, height: int, current_width: int, current_height: int) -> str:
    """Classify a preset relative to the OS-reported current
    resolution as one of "native"/"stretched"/"wide"/"high"/"low".

    - native: exact match to the current resolution.
    - stretched: same vertical resolution, narrower width (the classic
      Valorant "stretched res" shape -- same height, less horizontal pixels).
    - wide: a strictly wider aspect ratio than the current resolution.
    - high: the same aspect ratio as the current resolution (within the
      same tolerance used for "wide"), but strictly higher in both
      dimensions -- a proportional scale-up of the current resolution
      (e.g. 2560x1440 relative to a 1920x1080 current resolution). Without
      this bucket, a same-ratio candidate that is genuinely a higher
      resolution than the current one was indistinguishable from a
      genuinely lower-resolution candidate -- both fell into "low".
    - low: everything else (a narrower aspect ratio, or a same-ratio
      candidate that is not a scale-up), not a match.
    """
    if (width, height) == (current_width, current_height):
        return "native"
    if height == current_height and width < current_width:
        return "stretched"
    current_ratio = current_width / current_height
    candidate_ratio = width / height
    if candidate_ratio > current_ratio + 0.01:
        return "wide"
    if abs(candidate_ratio - current_ratio) <= 0.01 and width > current_width and height > current_height:
        return "high"
    return "low"


def get_current_resolution() -> tuple[int, int] | None:
    devmode = DEVMODE()
    devmode.dmSize = ctypes.sizeof(DEVMODE)
    if user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(devmode)):
        return devmode.dmPelsWidth, devmode.dmPelsHeight
    return None


def get_supported_resolutions() -> set:
    supported = set()
    devmode = DEVMODE()
    devmode.dmSize = ctypes.sizeof(DEVMODE)
    i = 0
    while user32.EnumDisplaySettingsW(None, i, ctypes.byref(devmode)):
        supported.add((devmode.dmPelsWidth, devmode.dmPelsHeight))
        i += 1
    return supported


def get_max_refresh_rate(width: int, height: int) -> int:
    """Highest registered dmDisplayFrequency among modes matching width x
    height exactly. Returns 0 when no registered mode matches, or when
    every match reports a placeholder rate. Per Win32, dmDisplayFrequency
    0 or 1 means "hardware default", not a real rate, so both are ignored.
    """
    best = 0
    devmode = DEVMODE()
    devmode.dmSize = ctypes.sizeof(DEVMODE)
    i = 0
    while user32.EnumDisplaySettingsW(None, i, ctypes.byref(devmode)):
        if (devmode.dmPelsWidth, devmode.dmPelsHeight) == (width, height):
            if devmode.dmDisplayFrequency > best:
                best = devmode.dmDisplayFrequency
        i += 1
    return best if best > 1 else 0


def _rollback_pending_registry_mode(width: int, height: int, frequency: int) -> None:
    """Revert a registry-level mode change that was written via
    `CDS_UPDATEREGISTRY | CDS_NORESET` but never actually applied, because
    the following `CDS_RESET` apply call failed. Without this, Windows
    keeps the new (unconfirmed, reported-as-failed) mode queued in the
    registry and can silently apply it on the next reboot, sleep/wake, or
    mode change, even though the caller was told the change failed.

    Re-runs the same write-then-apply sequence with `width`/`height`/
    `frequency` -- the resolution that was active before the call this is
    rolling back for started -- so a reported failure genuinely leaves the
    system in its prior state rather than a silently-queued future one. If
    the rollback itself fails, that is logged via `config.log_msg`: at that
    point the registry may still hold the queued failed mode with no
    further automatic recovery, which is worth distinguishing in the log
    from an ordinary `set_resolution` failure where the registry was never
    touched.
    """
    devmode = DEVMODE()
    devmode.dmSize = ctypes.sizeof(DEVMODE)
    devmode.dmPelsWidth = width
    devmode.dmPelsHeight = height
    devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    if frequency > 1:
        devmode.dmDisplayFrequency = frequency
        devmode.dmFields |= DM_DISPLAYFREQUENCY

    result = user32.ChangeDisplaySettingsW(
        ctypes.byref(devmode), CDS_UPDATEREGISTRY | CDS_NORESET
    )
    if result == 0:
        result = user32.ChangeDisplaySettingsW(None, CDS_RESET)

    if result != 0:
        log_msg(
            f"set_resolution: rollback to {width} x {height} after a failed "
            f"apply also failed (error code {result}) -- the registry may "
            f"still hold a queued resolution change that could get applied "
            f"on the next reboot, sleep/wake, or mode change."
        )


def set_resolution(width: int, height: int) -> tuple[bool, str]:
    devmode = DEVMODE()
    devmode.dmSize = ctypes.sizeof(DEVMODE)
    if not user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(devmode)):
        # Without a genuine current mode there is no real "original"
        # resolution to roll back to if the apply below fails. Proceeding
        # with a zero-initialized devmode would let a later failure queue a
        # bogus 0x0 mode into the registry via _rollback_pending_registry_mode,
        # so fail closed here instead of writing anything.
        return False, ("Could not read the current display settings, so the "
                        "resolution change was not attempted. Try again.")

    original_width = devmode.dmPelsWidth
    original_height = devmode.dmPelsHeight
    original_frequency = devmode.dmDisplayFrequency

    refresh_rate = get_max_refresh_rate(width, height)

    devmode.dmPelsWidth = width
    devmode.dmPelsHeight = height
    devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    if refresh_rate > 0:
        devmode.dmDisplayFrequency = refresh_rate
        devmode.dmFields |= DM_DISPLAYFREQUENCY

    result = user32.ChangeDisplaySettingsW(
        ctypes.byref(devmode), CDS_UPDATEREGISTRY | CDS_NORESET
    )

    if result != 0 and refresh_rate > 0:
        # Retry without forcing a refresh rate, as a genuine last resort
        # for the rare case where the driver rejects a rate it itself
        # enumerated for this exact mode.
        devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
        result = user32.ChangeDisplaySettingsW(
            ctypes.byref(devmode), CDS_UPDATEREGISTRY | CDS_NORESET
        )

    if result != 0:
        return False, (f"Error code {result}. Try running as Administrator, "
                        f"or that resolution isn't registered with your GPU driver.")

    result = user32.ChangeDisplaySettingsW(None, CDS_RESET)

    if result == 0:
        return True, f"Now running {width} x {height}"

    _rollback_pending_registry_mode(original_width, original_height, original_frequency)
    return False, (f"Error code {result}. Try running as Administrator, "
                    f"or that resolution isn't registered with your GPU driver.")


def detect_gpu_vendors() -> set:
    vendors = set()
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        output = result.stdout.lower()
        if "nvidia" in output:
            vendors.add("nvidia")
        if "amd" in output or "radeon" in output:
            vendors.add("amd")
        if "intel" in output:
            vendors.add("intel")
    except Exception as exc:
        log_msg(f"detect_gpu_vendors: GPU vendor detection via PowerShell failed: {exc!r}")
    return vendors


def _escape_ps_single_quoted(value: str) -> str:
    """Escape `value` for safe interpolation inside a PowerShell
    single-quoted string literal (e.g. `'*{value}*'`).

    PowerShell single-quoted strings are fully literal -- backticks, `$`,
    and `"` have no special meaning inside them. The one exception is `'`
    itself: a literal single quote must be written as `''` (doubled), or it
    closes the string early and anything after it is parsed as PowerShell
    code. Doubling every embedded `'` is therefore sufficient (and
    necessary) to keep `value` confined to its quoted literal.
    """
    return value.replace("'", "''")


def launch_appx_app(name_like: str, publisher_like: str = "") -> bool:
    try:
        name_like = _escape_ps_single_quoted(name_like)
        publisher_like = _escape_ps_single_quoted(publisher_like)
        publisher_clause = f" -and $_.Publisher -like '*{publisher_like}*'" if publisher_like else ""
        ps_cmd = (
            f"$pkg = Get-AppxPackage | Where-Object {{ $_.Name -like '*{name_like}*'{publisher_clause} }} "
            f"| Select-Object -First 1; "
            f"if ($pkg) {{ "
            f"$manifest = Get-AppxPackageManifest $pkg; "
            f"$appId = $manifest.Package.Applications.Application.Id; "
            f"Write-Output ($pkg.PackageFamilyName + '!' + $appId) "
            f"}}"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        aumid = result.stdout.strip()
        if aumid:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"])
            return True
    except Exception as exc:
        log_msg(f"launch_appx_app: failed to launch AppX app matching name_like={name_like!r}: {exc!r}")
    return False


def launch_start_app(*name_substrings: str) -> bool:
    for substring in name_substrings:
        try:
            safe_substring = _escape_ps_single_quoted(substring)
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-StartApps | Where-Object {{ $_.Name -like '*{safe_substring}*' }} "
                 f"| Select-Object -First 1 -ExpandProperty AppID)"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            aumid = result.stdout.strip()
            if aumid:
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"])
                return True
        except Exception as exc:
            log_msg(f"launch_start_app: failed to launch app matching substring={substring!r}: {exc!r}")
    return False


def open_nvidia_control_panel():
    legacy_path = r"C:\Program Files\NVIDIA Corporation\Control Panel Client\nvcplui.exe"
    if os.path.exists(legacy_path):
        subprocess.Popen([legacy_path])
        return
    if launch_start_app("NVIDIA Control Panel"):
        return
    webbrowser.open("https://www.nvidia.com/en-us/geforce/guides/nvidia-control-panel-quick-start-guide/")


def open_amd_software():
    paths = [
        r"C:\Program Files\AMD\CNext\CNext\RadeonSoftware.exe",
        r"C:\Program Files (x86)\AMD\CNext\CNext\RadeonSoftware.exe",
    ]
    for p in paths:
        if os.path.exists(p):
            subprocess.Popen([p])
            return
    if launch_start_app("AMD Software", "Radeon Software", "Radeon"):
        return
    webbrowser.open("https://www.amd.com/en/resources/support-articles/faqs/DH-032.html")


def open_intel_graphics_software():
    if launch_appx_app("Graphics", "Intel"):
        return
    if launch_start_app("Graphics Command Center", "Intel Graphics", "Command Center"):
        return
    webbrowser.open("https://www.intel.com/content/www/us/en/support/articles/000090440/graphics.html")
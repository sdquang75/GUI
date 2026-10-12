"""set_resolution() must not leave the registry holding a mode change that
was never actually applied. The Win32 sequence writes the new mode to the
registry (`CDS_UPDATEREGISTRY | CDS_NORESET`) and then applies it in a
separate call (`CDS_RESET`). If the registry write succeeds but the apply
call fails, Windows still has the new mode queued in the registry and can
silently apply it later (reboot, sleep/wake, next mode change) even though
`set_resolution()` reported failure to its caller.

These tests prove: when the apply call fails after a successful registry
write, `set_resolution()` attempts to roll the registry back to the
resolution that was active before the call, and it logs when that
rollback attempt itself also fails.
"""
import ctypes

from quickres import display


class ScriptedUser32:
    """Fake `user32` for CDS rollback tests. `current` is the OS-reported
    resolution/frequency returned for `ENUM_CURRENT_SETTINGS`. `fail_flags`
    maps a Win32 `flags` value to how many times `ChangeDisplaySettingsW`
    should report failure (result 1) the next times it's called with that
    exact `flags` value; once the count is exhausted, further calls with
    the same flags succeed (result 0). Every call is recorded in
    `change_calls` for assertions.
    """

    def __init__(self, current, fail_flags=None, fail_enum_current=False):
        self.current = current
        self._fail_flags = dict(fail_flags or {})
        self.fail_enum_current = fail_enum_current
        self.change_calls = []

    def EnumDisplaySettingsW(self, hdc, index, lp):
        if index == display.ENUM_CURRENT_SETTINGS:
            if self.fail_enum_current:
                return 0
            devmode = ctypes.cast(lp, ctypes.POINTER(display.DEVMODE)).contents
            width, height, freq = self.current
            devmode.dmPelsWidth = width
            devmode.dmPelsHeight = height
            devmode.dmDisplayFrequency = freq
            return 1
        return 0

    def ChangeDisplaySettingsW(self, lp, flags):
        if lp:
            devmode = ctypes.cast(lp, ctypes.POINTER(display.DEVMODE)).contents
            self.change_calls.append({
                "width": devmode.dmPelsWidth,
                "height": devmode.dmPelsHeight,
                "frequency": devmode.dmDisplayFrequency,
                "fields": devmode.dmFields,
                "flags": flags,
            })
        else:
            self.change_calls.append({"reset": True, "flags": flags})

        if self._fail_flags.get(flags, 0) > 0:
            self._fail_flags[flags] -= 1
            return 1
        return 0


def _registry_write_calls(fake):
    return [
        c for c in fake.change_calls
        if c.get("flags") == (display.CDS_UPDATEREGISTRY | display.CDS_NORESET)
    ]


def test_rollback_attempted_when_final_apply_fails_after_registry_write(monkeypatch):
    # Original/current resolution is 2560x1440; caller asks for 1920x1080.
    # The registry-write call succeeds, but the final apply (CDS_RESET) call
    # fails.
    fake = ScriptedUser32(current=(2560, 1440, 60), fail_flags={display.CDS_RESET: 1})
    monkeypatch.setattr(display, "user32", fake)
    monkeypatch.setattr(display, "get_max_refresh_rate", lambda w, h: 0)

    ok, message = display.set_resolution(1920, 1080)

    assert ok is False

    registry_calls = _registry_write_calls(fake)
    # First registry write requests the NEW resolution; the rollback's
    # registry write must be made afterward, targeting the ORIGINAL
    # resolution that was active before set_resolution() started.
    assert registry_calls[0]["width"] == 1920
    assert registry_calls[0]["height"] == 1080
    assert registry_calls[-1]["width"] == 2560
    assert registry_calls[-1]["height"] == 1440
    assert registry_calls[-1] is not registry_calls[0]

    reset_calls = [c for c in fake.change_calls if c.get("reset")]
    assert len(reset_calls) == 2  # the failed apply + the rollback's own apply


def test_rollback_failure_is_logged(monkeypatch):
    # Both the initial apply AND the rollback's own apply fail -- this must
    # be logged distinctly, since the registry may now hold the queued
    # (failed) mode with no further automatic recovery.
    fake = ScriptedUser32(current=(2560, 1440, 60), fail_flags={display.CDS_RESET: 2})
    monkeypatch.setattr(display, "user32", fake)
    monkeypatch.setattr(display, "get_max_refresh_rate", lambda w, h: 0)
    logged = []
    monkeypatch.setattr(display, "log_msg", lambda msg: logged.append(msg))

    ok, message = display.set_resolution(1920, 1080)

    assert ok is False
    assert len(logged) == 1
    assert "2560" in logged[0] and "1440" in logged[0]


def test_fails_closed_when_reading_current_resolution_fails(monkeypatch):
    # EnumDisplaySettingsW(ENUM_CURRENT_SETTINGS) itself fails (e.g. a
    # concurrent display-topology change). devmode is zero-initialized in
    # that case, so there is no genuine "original" resolution to roll back
    # to. set_resolution() must fail closed here -- returning an error
    # without writing anything to the registry -- rather than proceeding
    # with zeroed original_width/original_height/original_frequency, which
    # would let a later apply failure queue a bogus 0x0 mode via
    # _rollback_pending_registry_mode.
    fake = ScriptedUser32(current=(2560, 1440, 60), fail_enum_current=True)
    monkeypatch.setattr(display, "user32", fake)
    monkeypatch.setattr(display, "get_max_refresh_rate", lambda w, h: 0)
    rollback_calls = []
    monkeypatch.setattr(
        display, "_rollback_pending_registry_mode",
        lambda w, h, f: rollback_calls.append((w, h, f))
    )

    ok, message = display.set_resolution(1920, 1080)

    assert ok is False
    assert rollback_calls == []
    assert fake.change_calls == []  # no registry write was ever attempted


def test_no_rollback_attempted_when_apply_succeeds(monkeypatch):
    fake = ScriptedUser32(current=(2560, 1440, 60))
    monkeypatch.setattr(display, "user32", fake)
    monkeypatch.setattr(display, "get_max_refresh_rate", lambda w, h: 0)
    logged = []
    monkeypatch.setattr(display, "log_msg", lambda msg: logged.append(msg))

    ok, message = display.set_resolution(1920, 1080)

    assert ok is True
    assert len(_registry_write_calls(fake)) == 1  # no rollback registry write
    assert logged == []

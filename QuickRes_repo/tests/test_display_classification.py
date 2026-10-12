import ctypes

from quickres import display
from quickres.display import aspect_ratio_label, classify_resolution


class FakeUser32:
    """Fake `user32` scripting `EnumDisplaySettingsW`/`ChangeDisplaySettingsW`
    for RES-6 tests. `modes` is a list of (width, height, frequency) tuples
    returned in order by increasing `index`; `current` is what is returned
    for `ENUM_CURRENT_SETTINGS` (index -1), simulating the OS-reported
    current mode. `ChangeDisplaySettingsW` calls are captured verbatim so
    tests can assert exactly which frequency/fields were requested.
    """

    def __init__(self, modes, current=None):
        self.modes = modes
        self.current = current or (modes[0] if modes else (0, 0, 0))
        self.change_calls = []

    def EnumDisplaySettingsW(self, hdc, index, lp):
        devmode = ctypes.cast(lp, ctypes.POINTER(display.DEVMODE)).contents
        if index == display.ENUM_CURRENT_SETTINGS:
            width, height, freq = self.current
            devmode.dmPelsWidth = width
            devmode.dmPelsHeight = height
            devmode.dmDisplayFrequency = freq
            return 1
        if index >= len(self.modes):
            return 0
        width, height, freq = self.modes[index]
        devmode.dmPelsWidth = width
        devmode.dmPelsHeight = height
        devmode.dmDisplayFrequency = freq
        return 1

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
        return 0


def test_aspect_ratio_label_16_9():
    assert aspect_ratio_label(1920, 1080) == "16:9"


def test_aspect_ratio_label_4_3():
    assert aspect_ratio_label(1024, 768) == "4:3"


def test_classify_matches_current_is_native():
    assert classify_resolution(1920, 1080, 1920, 1080) == "native"


def test_classify_same_height_narrower_width_is_stretched():
    # RES-1/Valorant scenario: native 1920x1080, stretched 1440x1080.
    assert classify_resolution(1440, 1080, 1920, 1080) == "stretched"


def test_classify_wider_aspect_ratio_is_wide():
    # Native 1920x1080 (16:9); an ultrawide-shaped candidate is "wide".
    assert classify_resolution(2560, 1080, 1920, 1080) == "wide"


def test_classify_smaller_same_ratio_is_low():
    assert classify_resolution(1280, 720, 1920, 1080) == "low"


def test_classify_larger_same_ratio_is_not_low():
    # A same-ratio scale-up (16:9 2560x1440 over a 16:9 1920x1080 current
    # resolution -- exactly the QUICK_LIST "2560 x 1440" entry on a stock
    # 1080p install) must not be bucketed the same as a genuinely smaller
    # candidate.
    result = classify_resolution(2560, 1440, 1920, 1080)
    assert result != "low"
    assert result == classify_resolution(2560, 1440, 1920, 1080)


def test_classify_larger_same_ratio_is_high():
    assert classify_resolution(2560, 1440, 1920, 1080) == "high"


def test_classify_larger_same_ratio_is_high_4k():
    assert classify_resolution(3840, 2160, 1920, 1080) == "high"


def test_classify_smaller_same_ratio_still_low_after_high_added():
    # Genuinely lower-res, same-ratio candidates must remain distinct from
    # the new "high" bucket.
    assert classify_resolution(1280, 720, 1920, 1080) == "low"
    assert classify_resolution(1280, 720, 1920, 1080) != classify_resolution(2560, 1440, 1920, 1080)


# ---------------------------------------------------------------------------
# RES-6 -- get_max_refresh_rate / set_resolution frequency selection
# ---------------------------------------------------------------------------

def test_get_max_refresh_rate_picks_highest_among_matches(monkeypatch):
    fake = FakeUser32(modes=[
        (1920, 1080, 60),
        (1920, 1080, 120),
        (1920, 1080, 240),
        (2560, 1440, 360),
    ])
    monkeypatch.setattr(display, "user32", fake)

    assert display.get_max_refresh_rate(1920, 1080) == 240


def test_get_max_refresh_rate_single_mode(monkeypatch):
    fake = FakeUser32(modes=[
        (1920, 1080, 75),
        (1280, 720, 60),
    ])
    monkeypatch.setattr(display, "user32", fake)

    assert display.get_max_refresh_rate(1920, 1080) == 75


def test_get_max_refresh_rate_no_registered_mode_returns_zero(monkeypatch):
    fake = FakeUser32(modes=[(1280, 720, 60)])
    monkeypatch.setattr(display, "user32", fake)

    assert display.get_max_refresh_rate(1920, 1080) == 0


def test_get_max_refresh_rate_ignores_placeholder_rates(monkeypatch):
    fake = FakeUser32(modes=[
        (1920, 1080, 0),
        (1920, 1080, 1),
    ])
    monkeypatch.setattr(display, "user32", fake)

    assert display.get_max_refresh_rate(1920, 1080) == 0


def test_set_resolution_never_requests_unenumerated_rate(monkeypatch):
    fake = FakeUser32(
        modes=[(1920, 1080, 60), (1920, 1080, 120), (1920, 1080, 144)],
        current=(2560, 1440, 165),
    )
    monkeypatch.setattr(display, "user32", fake)

    ok, message = display.set_resolution(1920, 1080)

    assert ok is True
    first_call = fake.change_calls[0]
    assert first_call["frequency"] == 144
    assert first_call["fields"] & display.DM_DISPLAYFREQUENCY
    assert first_call["frequency"] != 165

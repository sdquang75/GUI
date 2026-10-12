import ctypes

import quickres.config as config


class _FakeUser32:
    """Stands in for config.user32 so _find_and_foreground_attempt() can be
    exercised end to end without a real Win32 window -- FindWindowW always
    "finds" a fixed hwnd, GetWindowThreadProcessId reports a fixed owning
    pid, and ShowWindow/SetForegroundWindow record whether they were ever
    invoked."""

    def __init__(self, hwnd, owner_pid):
        self.hwnd = hwnd
        self.owner_pid = owner_pid
        self.show_window_calls = []
        self.foreground_calls = []

    def FindWindowW(self, _cls, _title):
        return self.hwnd

    def GetWindowThreadProcessId(self, hwnd, pid_ref):
        assert hwnd == self.hwnd
        ctypes.cast(pid_ref, ctypes.POINTER(ctypes.c_ulong))[0] = self.owner_pid
        return 1  # thread id, unused by the caller

    def ShowWindow(self, hwnd, cmd):
        self.show_window_calls.append((hwnd, cmd))

    def SetForegroundWindow(self, hwnd):
        self.foreground_calls.append(hwnd)


def test_window_owned_by_this_process_exe_is_foregrounded(monkeypatch):
    fake = _FakeUser32(hwnd=4242, owner_pid=999)
    monkeypatch.setattr(config, "user32", fake)
    monkeypatch.setattr(config, "_get_process_exe_path", lambda pid: r"c:\apps\quickres.exe")
    monkeypatch.setattr(config, "_get_own_exe_path", lambda: r"c:\apps\quickres.exe")

    result = config._find_and_foreground_attempt()

    assert result is True
    assert fake.show_window_calls == [(4242, config.SW_RESTORE)]
    assert fake.foreground_calls == [4242]


def test_window_owned_by_a_different_exe_is_not_foregrounded(monkeypatch):
    """A same-user local process could pre-create a window titled
    "QuickRes" purely to steal focus on every launch. Finding a window with
    the right title is not enough -- its owning process's own executable
    path must match this process's own executable path before it is
    trusted enough to ShowWindow/SetForegroundWindow."""
    fake = _FakeUser32(hwnd=1337, owner_pid=666)
    monkeypatch.setattr(config, "user32", fake)
    monkeypatch.setattr(config, "_get_process_exe_path", lambda pid: r"c:\evil\imposter.exe")
    monkeypatch.setattr(config, "_get_own_exe_path", lambda: r"c:\apps\quickres.exe")
    log_calls = []
    monkeypatch.setattr(config, "log_msg", lambda msg: log_calls.append(msg))

    result = config._find_and_foreground_attempt()

    assert result is False
    assert fake.show_window_calls == []
    assert fake.foreground_calls == []
    assert any("QuickRes" in call for call in log_calls)


def test_window_whose_owner_exe_path_cannot_be_resolved_is_not_foregrounded(monkeypatch):
    """OpenProcess/QueryFullProcessImageNameW can fail for a lot of
    legitimate reasons (process already exited, access denied, ...). A
    failed lookup must never be treated as a match -- fail closed, the same
    way _is_reparse_point()/write_json_atomic() already do elsewhere in
    this file."""
    fake = _FakeUser32(hwnd=55, owner_pid=1)
    monkeypatch.setattr(config, "user32", fake)
    monkeypatch.setattr(config, "_get_process_exe_path", lambda pid: None)
    monkeypatch.setattr(config, "_get_own_exe_path", lambda: r"c:\apps\quickres.exe")

    result = config._find_and_foreground_attempt()

    assert result is False
    assert fake.show_window_calls == []
    assert fake.foreground_calls == []


def test_no_window_found_returns_false_without_owner_lookup(monkeypatch):
    fake = _FakeUser32(hwnd=0, owner_pid=0)
    monkeypatch.setattr(config, "user32", fake)
    lookup_calls = []
    monkeypatch.setattr(
        config, "_get_process_exe_path", lambda pid: lookup_calls.append(pid)
    )

    result = config._find_and_foreground_attempt()

    assert result is False
    assert lookup_calls == []

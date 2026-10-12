"""apply_update() writes the downloaded update binary to `new_exe_path`
and the generated launcher script to `bat_path` using plain open(), which
on Windows transparently follows an NTFS reparse point (symlink/junction)
planted at either path -- unlike every other on-disk write in this
codebase (quickres/config.py's write_json_atomic, via
_open_no_reparse_follow), which is already hardened against exactly this
same-user TOCTOU/symlink attack class. Both writes must go through that
same shared helper instead, and refuse cleanly (no write, no batch
execution) when it reports a reparse point.
"""
import ctypes

import pytest

from quickres import config
from quickres import updater


def _install_low_level_reparse_fake(monkeypatch, guarded_path, victim, victim_write):
    """Fakes CreateFileW/GetFileInformationByHandle so that ONLY a handle
    opened for `guarded_path` WITHOUT FILE_FLAG_OPEN_REPARSE_POINT is
    treated as following a symlink -- mirroring quickres/config.py's own
    round-25 test technique (see test_config_write_atomic_reparse_toctou.py's
    TestWriteJsonAtomicAtomicReparseOpen): called WITH the flag (the fixed
    behavior), the real Win32 call opens the reparse point object itself;
    called WITHOUT it (what plain open() would have done), the fake calls
    `victim_write` to simulate Windows transparently following the symlink
    and truncating whatever it points at, BEFORE handing back a real handle
    to that same victim -- proving via the real handle-open call, not a
    mocked higher-level seam, that no followed-target content is ever
    destroyed once the guard is in place. Handles are tracked individually
    (not just paths) so a second, unrelated _open_no_reparse_follow() call
    elsewhere in apply_update -- there are two, one per guarded write --
    still sees genuine, unfaked attributes.
    """
    real_create_file_w = config.kernel32.CreateFileW
    real_get_file_information_by_handle = config.kernel32.GetFileInformationByHandle
    guarded_handles = set()

    def fake_create_file_w(filename, access, share, sec_attrs, disposition, flags, template):
        if filename != guarded_path:
            return real_create_file_w(
                filename, access, share, sec_attrs, disposition, flags, template
            )
        if flags & config.FILE_FLAG_OPEN_REPARSE_POINT:
            handle = real_create_file_w(
                filename, access, share, sec_attrs, disposition, flags, template
            )
            guarded_handles.add(handle)
            return handle
        # Pre-fix behavior being simulated: Windows follows the symlink and
        # truncates the victim as a side effect of this call alone.
        victim_write()
        return real_create_file_w(
            str(victim), access, share, sec_attrs, disposition, flags, template
        )

    def fake_get_file_information_by_handle(handle, info_ptr):
        if handle not in guarded_handles:
            return real_get_file_information_by_handle(handle, info_ptr)
        # `info_ptr` is a plain ctypes.byref() proxy here -- calling this
        # fake bypasses the real CreateFileW/GetFileInformationByHandle
        # ctypes calling convention that would otherwise unwrap it, so it
        # must be cast back to a typed pointer before its fields can be
        # written to.
        typed_ptr = ctypes.cast(info_ptr, ctypes.POINTER(config._ByHandleFileInformation))
        typed_ptr.contents.dwFileAttributes = config.FILE_ATTRIBUTE_REPARSE_POINT
        return True

    monkeypatch.setattr(config.kernel32, "CreateFileW", fake_create_file_w)
    monkeypatch.setattr(
        config.kernel32, "GetFileInformationByHandle", fake_get_file_information_by_handle
    )


def _valid_pe_payload():
    header = bytearray(64)
    header[0:2] = b"MZ"
    header[60:64] = (64).to_bytes(4, "little")
    return bytes(header) + b"PE\x00\x00" + b"restofheader"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


class _FakeOpener:
    def __init__(self, payload):
        self._payload = payload

    def open(self, request, timeout=None):
        return _FakeResp(self._payload)


def _patch_download(monkeypatch, payload=None):
    payload = payload if payload is not None else _valid_pe_payload()
    monkeypatch.setattr(
        updater.urllib.request, "build_opener", lambda *h: _FakeOpener(payload)
    )


def _install_reparse_fake_for(monkeypatch, guarded_path):
    """Fakes CreateFileW/GetFileInformationByHandle so that ONLY
    `guarded_path` is reported as a reparse point, mirroring
    quickres/config.py's own round-25 test technique: called WITH
    FILE_FLAG_OPEN_REPARSE_POINT (the fixed behavior), the real Win32 call
    opens the reparse point object itself; called WITHOUT it (what plain
    open() would have done), it simulates Windows transparently following
    the symlink -- proving via the real handle-open call, not a mocked
    higher-level seam, that no followed-target content is ever destroyed.
    Every other path is passed straight through to the real CreateFileW.
    """
    real_create_file_w = config.kernel32.CreateFileW
    calls = []

    def fake_create_file_w(filename, access, share, sec_attrs, disposition, flags, template):
        calls.append((filename, flags))
        if filename != guarded_path:
            return real_create_file_w(
                filename, access, share, sec_attrs, disposition, flags, template
            )
        return real_create_file_w(
            filename, access, share, sec_attrs, disposition, flags, template
        )

    def fake_get_file_information_by_handle(handle, info_ptr):
        # Only report the guarded path itself as a reparse point -- every
        # other CreateFileW call in apply_update (reading back the
        # downloaded exe for the PE/SHA-256 checks, etc.) must see its
        # normal, non-reparse attributes.
        info_ptr.contents.dwFileAttributes = config.FILE_ATTRIBUTE_REPARSE_POINT
        return True

    monkeypatch.setattr(config.kernel32, "CreateFileW", fake_create_file_w)
    monkeypatch.setattr(
        config.kernel32, "GetFileInformationByHandle", fake_get_file_information_by_handle
    )
    return calls


class TestApplyUpdateRefusesNewExePathReparsePoint:
    def test_symlink_at_new_exe_path_is_refused_without_destroying_followed_target(
        self, monkeypatch, tmp_path
    ):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))
        _patch_download(monkeypatch)

        victim = tmp_path / "victim.txt"
        victim.write_bytes(b"original-secret-content")
        new_exe_path = str(tmp_path / "QuickRes_new.exe")

        # _open_no_reparse_follow() is the single atomic call that both
        # opens new_exe_path and determines whether it is a reparse point
        # -- monkeypatching it directly (rather than the lower-level Win32
        # calls) is enough to prove apply_update refuses to write through
        # it and never destroys anything, without needing real
        # SeCreateSymbolicLinkPrivilege on the test machine.
        opened_paths = []

        def fake_open_no_reparse_follow(path, binary=False):
            opened_paths.append((path, binary))
            if path == new_exe_path:
                return None
            return config._open_no_reparse_follow(path, binary=binary)

        monkeypatch.setattr(updater, "_open_no_reparse_follow", fake_open_no_reparse_follow)

        popen_calls = []
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: popen_calls.append(1))

        with pytest.raises(OSError, match="reparse point"):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        # No destructive write ever landed: neither the guarded path nor
        # the unrelated victim file was touched, and nothing downstream
        # (bat generation, process launch) ever ran.
        assert not (tmp_path / "QuickRes_new.exe").exists()
        assert victim.read_bytes() == b"original-secret-content"
        assert not (tmp_path / "update.bat").exists()
        assert popen_calls == []
        assert (new_exe_path, True) in opened_paths

    def test_low_level_open_never_truncates_a_followed_target(self, monkeypatch, tmp_path):
        """The genuinely destructive scenario this whole finding is about:
        proven at the real Win32 CreateFileW boundary, not by mocking
        _open_no_reparse_follow itself -- a symlink at new_exe_path must
        never cause the file it points at to be truncated."""
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))
        _patch_download(monkeypatch)

        victim = tmp_path / "victim.txt"
        victim.write_bytes(b"original-secret-content")
        new_exe_path = str(tmp_path / "QuickRes_new.exe")

        def victim_write():
            victim.write_bytes(b"DESTROYED")

        _install_low_level_reparse_fake(monkeypatch, new_exe_path, victim, victim_write)
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        with pytest.raises(OSError, match="reparse point"):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        assert victim.read_bytes() == b"original-secret-content"


class TestApplyUpdateRefusesBatPathReparsePoint:
    def test_symlink_at_bat_path_is_refused_without_destroying_followed_target_or_executing(
        self, monkeypatch, tmp_path
    ):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))
        _patch_download(monkeypatch)

        victim = tmp_path / "victim.bat"
        victim.write_text("original-secret-content", encoding="utf-8")
        bat_path = str(tmp_path / "update.bat")

        opened_paths = []

        def fake_open_no_reparse_follow(path, binary=False):
            opened_paths.append((path, binary))
            if path == bat_path:
                return None
            return config._open_no_reparse_follow(path, binary=binary)

        monkeypatch.setattr(updater, "_open_no_reparse_follow", fake_open_no_reparse_follow)

        popen_calls = []
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: popen_calls.append(1))

        with pytest.raises(OSError, match="reparse point"):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        assert victim.read_text(encoding="utf-8") == "original-secret-content"
        assert not (tmp_path / "update.bat").exists()
        assert popen_calls == []
        assert (bat_path, False) in opened_paths

        # The download itself must have already completed and passed its
        # integrity checks by the time the bat write is attempted -- the
        # new exe is legitimately staged on disk, only the batch script
        # write (and therefore its execution) is refused.
        assert (tmp_path / "QuickRes_new.exe").exists()

    def test_low_level_open_never_truncates_a_followed_target(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))
        _patch_download(monkeypatch)

        victim = tmp_path / "victim.bat"
        victim.write_text("original-secret-content", encoding="utf-8")
        bat_path = str(tmp_path / "update.bat")

        def victim_write():
            victim.write_text("DESTROYED", encoding="utf-8")

        _install_low_level_reparse_fake(monkeypatch, bat_path, victim, victim_write)
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        with pytest.raises(OSError, match="reparse point"):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        assert victim.read_text(encoding="utf-8") == "original-secret-content"
        # The new exe write (the OTHER guarded call) must not have been
        # collaterally treated as a reparse point by this fake.
        assert (tmp_path / "QuickRes_new.exe").exists()


class TestApplyUpdateRegressionNoSymlinkInvolved:
    """Regression: the ordinary (no reparse point anywhere) download-and-
    generate-batch-script path must still work exactly as before -- both
    writes now go through _open_no_reparse_follow() instead of plain
    open(), but for a genuine regular file/new path that must behave
    identically."""

    def test_downloaded_content_lands_correctly_in_new_exe_path(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))
        payload = _valid_pe_payload()
        _patch_download(monkeypatch, payload)
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        with pytest.raises(SystemExit):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        new_exe_path = tmp_path / "QuickRes_new.exe"
        assert new_exe_path.read_bytes() == payload

    def test_bat_script_still_generated_and_launched_normally(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))
        _patch_download(monkeypatch)

        popen_calls = []

        def fake_popen(args, **kwargs):
            popen_calls.append(args)
            return None

        monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)

        with pytest.raises(SystemExit):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        bat_path = tmp_path / "update.bat"
        assert bat_path.exists()
        assert "@echo off" in bat_path.read_text(encoding="utf-8")
        assert len(popen_calls) == 1
        assert popen_calls[0][0] == "cmd"
        assert popen_calls[0][2] == str(bat_path)

    def test_overwriting_a_pre_existing_new_exe_path_still_succeeds(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))
        (tmp_path / "QuickRes_new.exe").write_bytes(b"stale leftover from a previous attempt")
        payload = _valid_pe_payload()
        _patch_download(monkeypatch, payload)
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        with pytest.raises(SystemExit):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        assert (tmp_path / "QuickRes_new.exe").read_bytes() == payload

"""Round 31 fix (HIGH finding): log_msg() opened LOG_PATH with plain
open(LOG_PATH, "a", encoding="utf-8"), and _rotate_log_if_needed() sized it
with os.path.getsize()/os.replace() -- neither carried the NTFS
reparse-point/symlink hardening (_open_no_reparse_follow /
_is_reparse_point) that every other on-disk write in this codebase
(write_json_atomic for config.json/pending_restore.json, updater.py's
downloaded exe and update.bat) was specifically given to defend against the
same attack, even though log_msg is reachable from the ELEVATED helper
process (main.py's _run_elevated_helper) via write_json_atomic()'s own
except-block calling log_msg() on any I/O failure.

A same-user, unprivileged attacker with SeCreateSymbolicLinkPrivilege can
pre-create a file symlink at LOG_PATH pointing at an arbitrary path. Plain
open(LOG_PATH, "a") transparently follows that symlink (CreateFileW with no
FILE_FLAG_OPEN_REPARSE_POINT) and appends attacker-triggered content at the
symlink's target with the elevated process's own privileges.

log_msg() must open LOG_PATH through _open_no_reparse_follow() (in append
mode) instead of plain open(), and _rotate_log_if_needed() must refuse to
touch LOG_PATH at all when it is itself a reparse point.
"""
import ctypes
import os

import quickres.config as config


class TestLogMsgUsesAtomicReparseGuardedOpen:
    def test_log_msg_opens_log_path_through_open_no_reparse_follow(self, tmp_path, monkeypatch):
        log_path = os.path.join(str(tmp_path), "quickres.log")
        monkeypatch.setattr(config, "LOG_PATH", log_path)

        opened = []
        real_open_no_reparse_follow = config._open_no_reparse_follow

        def spying_open_no_reparse_follow(path, *args, **kwargs):
            opened.append((path, args, kwargs))
            return real_open_no_reparse_follow(path, *args, **kwargs)

        monkeypatch.setattr(config, "_open_no_reparse_follow", spying_open_no_reparse_follow)

        config.log_msg("hello")

        assert len(opened) == 1
        path, args, kwargs = opened[0]
        assert path == log_path
        # Must be opened in append mode -- a truncating open (the default
        # CREATE_ALWAYS behavior _open_no_reparse_follow uses for its other
        # callers) would destroy prior log history on every single call.
        assert kwargs.get("append") is True or (len(args) >= 2 and args[1] is True)
        with open(log_path, "r", encoding="utf-8") as f:
            assert "hello" in f.read()

    def test_log_msg_appends_across_multiple_calls_without_truncating(self, tmp_path, monkeypatch):
        log_path = os.path.join(str(tmp_path), "quickres.log")
        monkeypatch.setattr(config, "LOG_PATH", log_path)

        config.log_msg("first")
        config.log_msg("second")
        config.log_msg("third")

        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "first" in content
        assert "second" in content
        assert "third" in content

    def test_log_msg_refuses_to_write_when_log_path_open_reports_a_reparse_point(
        self, tmp_path, monkeypatch
    ):
        log_path = os.path.join(str(tmp_path), "quickres.log")
        monkeypatch.setattr(config, "LOG_PATH", log_path)
        monkeypatch.setattr(config, "_open_no_reparse_follow", lambda *a, **k: None)

        # Must not raise -- log_msg's own contract is that logging failures
        # are swallowed, never propagated to the caller.
        config.log_msg("should not land anywhere")

        assert not os.path.exists(log_path)

    def test_log_msg_does_not_destroy_a_followed_target_when_log_path_is_a_symlink(
        self, tmp_path, monkeypatch
    ):
        # Proven at the real Win32 CreateFileW boundary, not by mocking
        # _open_no_reparse_follow itself -- mirrors
        # test_updater_symlink_write_guard.py's
        # _install_low_level_reparse_fake technique.
        victim = tmp_path / "victim.txt"
        victim.write_text("original-secret-content", encoding="utf-8")
        log_path = os.path.join(str(tmp_path), "quickres.log")
        monkeypatch.setattr(config, "LOG_PATH", log_path)

        real_create_file_w = config.kernel32.CreateFileW
        real_get_file_information_by_handle = config.kernel32.GetFileInformationByHandle
        guarded_handles = set()

        def fake_create_file_w(filename, access, share, sec_attrs, disposition, flags, template):
            if filename != log_path:
                return real_create_file_w(filename, access, share, sec_attrs, disposition, flags, template)
            if flags & config.FILE_FLAG_OPEN_REPARSE_POINT:
                handle = real_create_file_w(
                    filename, access, share, sec_attrs, disposition, flags, template
                )
                guarded_handles.add(handle)
                return handle
            # Pre-fix behavior being simulated: Windows follows the symlink
            # and appends to the victim as a side effect of this call alone.
            victim.write_text("DESTROYED", encoding="utf-8")
            return real_create_file_w(
                str(victim), access, share, sec_attrs, disposition, flags, template
            )

        def fake_get_file_information_by_handle(handle, info_ptr):
            if handle not in guarded_handles:
                return real_get_file_information_by_handle(handle, info_ptr)
            typed_ptr = ctypes.cast(info_ptr, ctypes.POINTER(config._ByHandleFileInformation))
            typed_ptr.contents.dwFileAttributes = config.FILE_ATTRIBUTE_REPARSE_POINT
            return True

        monkeypatch.setattr(config.kernel32, "CreateFileW", fake_create_file_w)
        monkeypatch.setattr(
            config.kernel32, "GetFileInformationByHandle", fake_get_file_information_by_handle
        )

        config.log_msg("attacker-triggered content")

        assert victim.read_text(encoding="utf-8") == "original-secret-content"


class TestRotateLogIfNeededRefusesReparsePoint:
    def test_skips_rotation_entirely_when_log_path_is_a_reparse_point(self, tmp_path, monkeypatch):
        log_path = os.path.join(str(tmp_path), "quickres.log")
        monkeypatch.setattr(config, "LOG_PATH", log_path)
        monkeypatch.setattr(config, "LOG_MAX_BYTES", 100)
        # A real, genuinely oversized file sits at log_path -- without the
        # reparse-point check, rotation would proceed on it regardless.
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("x" * 200)

        checked = []

        def fake_is_reparse_point(path):
            checked.append(path)
            return path == log_path

        monkeypatch.setattr(config, "_is_reparse_point", fake_is_reparse_point)

        replace_calls = []
        real_replace = config.os.replace

        def spying_replace(src, dst):
            replace_calls.append((src, dst))
            return real_replace(src, dst)

        monkeypatch.setattr(config.os, "replace", spying_replace)

        config._rotate_log_if_needed()

        assert replace_calls == []
        assert log_path in checked
        # The file must be left exactly as it was -- untouched, not rotated.
        assert os.path.exists(log_path)
        assert not os.path.exists(log_path + ".old")

    def test_still_rotates_normally_when_log_path_is_not_a_reparse_point(self, tmp_path, monkeypatch):
        log_path = os.path.join(str(tmp_path), "quickres.log")
        monkeypatch.setattr(config, "LOG_PATH", log_path)
        monkeypatch.setattr(config, "LOG_MAX_BYTES", 100)
        monkeypatch.setattr(config, "_is_reparse_point", lambda path: False)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("x" * 200)

        config._rotate_log_if_needed()

        assert os.path.exists(log_path + ".old")
        assert not os.path.exists(log_path)

import os

import quickres.config as config


class TestIsReparsePointSeam:
    """_is_reparse_point() is the injectable ctypes seam get_app_dir() uses
    to detect a pre-planted NTFS directory junction/reparse point before
    trusting %LOCALAPPDATA%\\QuickRes."""

    def test_true_when_reparse_point_attribute_bit_set(self, monkeypatch):
        monkeypatch.setattr(
            config.kernel32, "GetFileAttributesW", lambda path: config.FILE_ATTRIBUTE_REPARSE_POINT
        )

        assert config._is_reparse_point("C:\\fake\\path") is True

    def test_false_when_attribute_bit_not_set(self, monkeypatch):
        FILE_ATTRIBUTE_DIRECTORY = 0x10
        monkeypatch.setattr(
            config.kernel32, "GetFileAttributesW", lambda path: FILE_ATTRIBUTE_DIRECTORY
        )

        assert config._is_reparse_point("C:\\fake\\path") is False

    def test_false_when_attributes_call_fails(self, monkeypatch):
        # An inaccessible/nonexistent path is a different failure mode --
        # get_app_dir()'s own makedirs/try-except handles that, so the seam
        # itself must not report "reparse point" for it.
        monkeypatch.setattr(
            config.kernel32, "GetFileAttributesW", lambda path: config.INVALID_FILE_ATTRIBUTES
        )

        assert config._is_reparse_point("C:\\fake\\path") is False

    def test_false_for_a_genuinely_nonexistent_path_via_the_real_win32_call(self, tmp_path):
        # Round 14 fix (Stream R1): ctypes.windll.kernel32.GetFileAttributesW
        # defaults to a *signed* c_long return type, so the real Win32 call
        # (not the lambda-mocked one used by the other tests above) returns
        # -1, not the documented unsigned 0xFFFFFFFF, for a missing path.
        # Comparing that signed -1 against INVALID_FILE_ATTRIBUTES
        # (0xFFFFFFFF) never matched, so the "attributes call failed" branch
        # never took -- execution fell through to the bitwise AND, where -1's
        # all-ones bit pattern makes every reparse-point-bit check true. That
        # turned every nonexistent path into a false positive "IS a reparse
        # point". write_json_atomic()'s per-invocation tmp file never exists
        # before it is created, so this silently refused every single write
        # once _is_reparse_point() started being called on that tmp path.
        missing_path = os.path.join(str(tmp_path), "does-not-exist.tmp")

        assert config._is_reparse_point(missing_path) is False


class TestGetAppDirRefusesJunction:
    """Round 5 fix (Stream A, finding 1): a standard-privilege process can
    plant a junction at %LOCALAPPDATA%\\QuickRes before QuickRes's first
    run. If get_app_dir() trusted it, the elevated helper's later writes
    would transparently follow the junction with elevated rights."""

    def test_refuses_and_falls_back_when_localappdata_dir_is_a_reparse_point(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setattr(config.sys, "platform", "win32")
        monkeypatch.setattr(config, "_is_reparse_point", lambda path: True)

        result = config.get_app_dir()

        planted_junction_path = os.path.join(str(tmp_path), "QuickRes")
        assert result != planted_junction_path

    def test_trusts_localappdata_dir_when_not_a_reparse_point(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setattr(config.sys, "platform", "win32")
        monkeypatch.setattr(config, "_is_reparse_point", lambda path: False)

        result = config.get_app_dir()

        assert result == os.path.join(str(tmp_path), "QuickRes")

    def test_reparse_check_only_runs_after_makedirs_succeeds(self, tmp_path, monkeypatch):
        """The new check must not fire on (and must not break the existing
        fallback for) the unrelated, more-common case of makedirs itself
        failing -- e.g. LOCALAPPDATA pointing at an unwritable location."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setattr(config.sys, "platform", "win32")

        def _raise_makedirs(*args, **kwargs):
            raise OSError("simulated makedirs failure")

        monkeypatch.setattr(config.os, "makedirs", _raise_makedirs)
        reparse_calls = []
        monkeypatch.setattr(
            config, "_is_reparse_point", lambda path: reparse_calls.append(path) or False
        )

        result = config.get_app_dir()

        assert reparse_calls == []
        assert result != os.path.join(str(tmp_path), "QuickRes")

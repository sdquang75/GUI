"""Round 2 corrective fixes for quickres/updater.py (Stream 3):

1. `apply_update` must verify the downloaded bytes are at least a plausible
   Windows PE executable (DOS 'MZ' magic + PE header signature) before
   staging/launching it. A truncated/corrupted-but-200-OK response body must
   be rejected and cleaned up, not written to disk and executed.
2. The generated update.bat script must NOT unconditionally delete the
   `.old` backup immediately after the move succeeds -- it must only delete
   the backup after confirming the exact newly-started process is healthy, so
   a corrupt new exe still
   leaves a usable rollback.
"""
import pytest

from quickres import updater


class TestLooksLikePeExecutable:
    def test_rejects_file_without_mz_magic(self, tmp_path):
        bad = tmp_path / "not_an_exe.bin"
        bad.write_bytes(b"newdata")

        assert updater._looks_like_pe_executable(str(bad)) is False

    def test_rejects_truncated_file_shorter_than_dos_header(self, tmp_path):
        bad = tmp_path / "truncated.bin"
        bad.write_bytes(b"MZ")

        assert updater._looks_like_pe_executable(str(bad)) is False

    def test_rejects_mz_magic_without_valid_pe_signature(self, tmp_path):
        # 64-byte DOS header with 'MZ' magic, but e_lfanew (offset 0x3C)
        # points at garbage that isn't 'PE\0\0'.
        bad = tmp_path / "fake_mz.bin"
        header = bytearray(64)
        header[0:2] = b"MZ"
        header[60:64] = (64).to_bytes(4, "little")
        bad.write_bytes(bytes(header) + b"NOPE")

        assert updater._looks_like_pe_executable(str(bad)) is False

    def test_rejects_nonexistent_file(self, tmp_path):
        missing = tmp_path / "does_not_exist.exe"

        assert updater._looks_like_pe_executable(str(missing)) is False

    def test_accepts_minimal_valid_dos_pe_header(self, tmp_path):
        good = tmp_path / "valid.exe"
        header = bytearray(64)
        header[0:2] = b"MZ"
        header[60:64] = (64).to_bytes(4, "little")
        good.write_bytes(bytes(header) + b"PE\x00\x00" + b"restofheader")

        assert updater._looks_like_pe_executable(str(good)) is True


class TestApplyUpdateRejectsCorruptDownload:
    def _patch_download(self, monkeypatch, payload: bytes):
        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return payload

        # Round 3: apply_update downloads via a dedicated opener
        # (urllib.request.build_opener(_AllowlistRedirectHandler())), not
        # the bare module-level urlopen() -- mock at that integration
        # point so this fixture still exercises the real code path.
        class _FakeOpener:
            def open(self, request, timeout=None):
                return _FakeResp()

        monkeypatch.setattr(
            updater.urllib.request, "build_opener", lambda *h: _FakeOpener()
        )

    def test_corrupt_download_raises_and_removes_new_exe_before_staging(
        self, monkeypatch, tmp_path
    ):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))
        self._patch_download(monkeypatch, b"this is not a PE file at all")

        popen_calls = []
        monkeypatch.setattr(
            updater.subprocess, "Popen", lambda *a, **k: popen_calls.append(1)
        )

        with pytest.raises(ValueError, match="integrity"):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        # Fail closed: the bad download must be cleaned up and the batch
        # script must never be staged/launched.
        new_exe_path = tmp_path / "QuickRes_new.exe"
        assert not new_exe_path.exists()
        bat_path = tmp_path / "update.bat"
        assert not bat_path.exists()
        assert popen_calls == []

    def test_valid_pe_download_proceeds_to_staging(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))

        header = bytearray(64)
        header[0:2] = b"MZ"
        header[60:64] = (64).to_bytes(4, "little")
        valid_payload = bytes(header) + b"PE\x00\x00" + b"restofheader"
        self._patch_download(monkeypatch, valid_payload)
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        with pytest.raises(SystemExit):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        bat_path = tmp_path / "update.bat"
        assert bat_path.exists()


class TestUpdateBatDeferredBackupDeletion:
    def _generated_script(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))

        header = bytearray(64)
        header[0:2] = b"MZ"
        header[60:64] = (64).to_bytes(4, "little")
        valid_payload = bytes(header) + b"PE\x00\x00" + b"restofheader"

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return valid_payload

        # Round 3: mock at the build_opener()/opener.open() integration
        # point apply_update now actually uses (see
        # test_updater_download_allowlist.py's redirect-handler tests).
        class _FakeOpener:
            def open(self, request, timeout=None):
                return _FakeResp()

        monkeypatch.setattr(
            updater.urllib.request, "build_opener", lambda *h: _FakeOpener()
        )
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        with pytest.raises(SystemExit):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        bat_path = tmp_path / "update.bat"
        return bat_path.read_text()

    def test_move_step_no_longer_unconditionally_deletes_backup(
        self, monkeypatch, tmp_path
    ):
        script = self._generated_script(monkeypatch, tmp_path)
        lines = script.splitlines()

        move_idx = next(
            i for i, line in enumerate(lines) if line.strip().startswith("move /y")
        )
        # The line(s) immediately following the move (up to the next
        # errorlevel check / label) must NOT unconditionally delete the
        # ".old" backup -- deletion must be gated on launch confirmation.
        following = lines[move_idx + 1]
        assert following.strip().lower() != f'del "{tmp_path / "QuickRes.exe.old"}"'.lower()
        unconditional_del_after_move = (
            following.strip().lower().startswith("del ")
            and "errorlevel" not in following.lower()
        )
        assert not unconditional_del_after_move

    def test_backup_deletion_is_gated_on_launch_healthcheck_confirmation(
        self, monkeypatch, tmp_path
    ):
        script = self._generated_script(monkeypatch, tmp_path)
        lines = [l.strip() for l in script.splitlines()]

        move_idx = next(
            i for i, line in enumerate(lines) if line.lower().startswith("move /y")
        )
        healthcheck_idx = next(
            i
            for i, line in enumerate(lines)
            if "start-process -filepath" in line.lower()
        )
        # Only consider backup-deletion steps that occur AFTER the move --
        # the pre-existing "if exist .old del .old" stale-backup cleanup at
        # the very top of the script (before the rename even happens) is
        # unrelated and must not be mistaken for the post-move deletion.
        old_backup_needle = str(tmp_path / "QuickRes.exe.old").lower()
        del_backup_indices = [
            i
            for i, line in enumerate(lines)
            if i > move_idx and "del " in line.lower() and old_backup_needle in line.lower()
        ]

        assert del_backup_indices, "expected a backup-deletion step in the script"
        # Every post-move backup-deletion step must appear AFTER the exact
        # process health check, not immediately after the move.
        assert all(idx > healthcheck_idx for idx in del_backup_indices)

    def test_script_still_self_deletes(self, monkeypatch, tmp_path):
        script = self._generated_script(monkeypatch, tmp_path)
        assert 'del "%~f0"' in script

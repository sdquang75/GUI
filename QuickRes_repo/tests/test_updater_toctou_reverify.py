"""Round 6 corrective fix for quickres/updater.py (Stream 3):

MEDIUM finding -- TOCTOU window between validation and replace-and-relaunch.
The PE-header + optional SHA-256 checks in `apply_update` run exactly once,
immediately after download, but the actual replace-and-relaunch used to
happen up to 5 seconds later inside the generated `update.bat` (a fixed
`timeout /t 5` existed to let the currently-running exe release its file
handle before `ren` could rename it away). In the typical portable
no-install install, that directory is an ordinary user-writable folder, so
another local process with write access to it had a multi-second window to
swap the already-validated QuickRes_new.exe for a malicious file before the
batch script ever moved it into place.

Two complementary mitigations are verified here:
1. The fixed 5-second sleep before the rename is replaced with a short
   polling loop that retries the rename itself as soon as the file lock
   releases (faster in the common case; same worst-case ceiling).
2. A lightweight `powershell -Command` one-liner re-verifies the staged
   QuickRes_new.exe's PE header (and its SHA-256 again, if an expected hash
   was supplied) immediately before the `move /y` that stages it, so a file
   swapped into place during the wait is caught right before it would ever
   run.
"""
import hashlib

from quickres import updater


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


def _generated_script(monkeypatch, tmp_path, version_info=None):
    fake_exe = tmp_path / "QuickRes.exe"
    fake_exe.write_bytes(b"old")
    monkeypatch.setattr(updater.sys, "executable", str(fake_exe))

    payload = _valid_pe_payload()

    class _FakeOpener:
        def open(self, request, timeout=None):
            return _FakeResp(payload)

    monkeypatch.setattr(
        updater.urllib.request, "build_opener", lambda *h: _FakeOpener()
    )
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

    import pytest

    with pytest.raises(SystemExit):
        updater.apply_update(
            "https://lxzy.my/QuickRes_new.exe", version_info=version_info
        )

    bat_path = tmp_path / "update.bat"
    return bat_path.read_text(), payload


class TestFixedSleepReplacedWithPollingRetry:
    def test_no_fixed_five_second_sleep_before_rename(self, monkeypatch, tmp_path):
        script, _ = _generated_script(monkeypatch, tmp_path)

        # The old unconditional `timeout /t 5 /nobreak >nul` (blind sleep
        # before ever attempting the rename) must be gone.
        assert "timeout /t 5" not in script

    def test_rename_step_is_retried_in_a_polling_loop(self, monkeypatch, tmp_path):
        script, _ = _generated_script(monkeypatch, tmp_path)
        lines = [l.strip() for l in script.splitlines()]

        ren_indices = [
            i for i, line in enumerate(lines) if line.lower().startswith("ren ")
        ]
        assert ren_indices, "expected a ren step in the script"
        ren_idx = ren_indices[0]

        # A retry loop needs: a short wait after a failed rename, and a
        # jump back to retry the rename again, rather than giving up
        # immediately.
        after = "\n".join(lines[ren_idx:])
        assert "timeout /t 1" in after
        # The rename attempt must be reachable via a goto (i.e. actually
        # looped), not a single one-shot attempt.
        assert "goto" in after.lower()


class TestReverificationBeforeMove:
    def test_powershell_reverify_step_present_before_move(
        self, monkeypatch, tmp_path
    ):
        script, _ = _generated_script(monkeypatch, tmp_path)
        lines = [l.strip() for l in script.splitlines()]

        move_idx = next(
            i for i, line in enumerate(lines) if line.lower().startswith("move /y")
        )
        reverify_indices = [
            i
            for i, line in enumerate(lines)
            if line.lower().startswith("powershell") and "readallbytes" in line.lower()
        ]
        assert reverify_indices, "expected a PowerShell PE re-verification step"
        assert all(idx < move_idx for idx in reverify_indices)

        # The move must be gated on the re-verification step's outcome, not
        # run unconditionally right after it.
        ps_idx = reverify_indices[0]
        following = lines[ps_idx + 1]
        assert "errorlevel" in following.lower()

    def test_reverify_checks_pe_header_without_expected_hash(
        self, monkeypatch, tmp_path
    ):
        script, _ = _generated_script(monkeypatch, tmp_path)
        ps_line = next(
            l for l in script.splitlines() if l.strip().lower().startswith("powershell")
        )
        # Structural PE-header re-check (MZ magic byte values) must be
        # present even when no expected hash was supplied.
        assert "0x4D" in ps_line or "0x4d" in ps_line.lower()
        assert "Get-FileHash" not in ps_line

    def test_reverify_checks_sha256_when_expected_hash_supplied(
        self, monkeypatch, tmp_path
    ):
        expected = hashlib.sha256(_valid_pe_payload()).hexdigest()
        script, _ = _generated_script(
            monkeypatch, tmp_path, version_info={"sha256": expected}
        )
        ps_line = next(
            l for l in script.splitlines() if l.strip().lower().startswith("powershell")
        )
        assert "Get-FileHash" in ps_line
        assert expected.upper() in ps_line

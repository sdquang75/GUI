"""Launch confirmation uses the exact process, not a slow global name scan.

The old batch script repeatedly searched ``tasklist`` for an image name. A
failed launch could therefore spend 36 seconds polling, and a portable copy
on the Desktop could be delayed or misdetected by OneDrive/Defender. The
generated script now starts the replacement through ``Start-Process
-PassThru`` and checks that exact process is still alive after a short health
window. It preserves rollback, with one bounded retry for transient launch
failures.
"""
import pytest

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


def _generated_script(monkeypatch, exe_dir, exe_name="QuickRes.exe"):
    exe_dir.mkdir(parents=True, exist_ok=True)
    fake_exe = exe_dir / exe_name
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

    with pytest.raises(SystemExit):
        updater.apply_update("https://lxzy.my/QuickRes_new.exe")

    return (exe_dir / "update.bat").read_text()


def _launch_section(script):
    lines = script.splitlines()
    lower = [line.strip().lower() for line in lines]
    launch_idx = lower.index(":launch")
    launchfail_idx = lower.index(":launchfail")
    assert launchfail_idx > launch_idx
    return lines[launch_idx : launchfail_idx + 1]


class TestLaunchHealthCheck:
    def test_uses_exact_started_process_instead_of_tasklist(self, monkeypatch, tmp_path):
        script = _generated_script(monkeypatch, tmp_path)
        section = "\n".join(line.strip() for line in _launch_section(script)).lower()

        assert "start-process -filepath" in section
        assert "-workingdirectory" in section
        assert "-passthru" in section
        assert "start-sleep -seconds 2" in section
        assert "tasklist" not in section
        assert "launchpoll" not in section

    def test_has_one_short_retry_before_rollback(self, monkeypatch, tmp_path):
        script = _generated_script(monkeypatch, tmp_path)
        section = "\n".join(line.strip() for line in _launch_section(script)).lower()

        assert "set launchretries=0" in section
        assert "set /a launchretries+=1" in section
        assert "if %launchretries% geq 2 goto :launchfail" in section
        assert "timeout /t 1 /nobreak >nul" in section
        assert "goto :launchtry" in section

    def test_escapes_executable_and_working_directory_for_powershell(self):
        malicious = r"C:\Users\evil' ; Remove-Item C:\ -Recurse -Force #\QuickRes.exe"
        command = updater._build_launch_healthcheck_command(malicious)

        assert "evil'' ;" in command
        assert "evil' ;" not in command

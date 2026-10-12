"""Round 7 corrective fixes for quickres/updater.py (Stream 1):

1. `_build_reverify_command`'s PowerShell one-liner and the generated
   update.bat's plain batch commands both interpolate the exe's own
   install-directory path unescaped. A single quote in that path breaks
   out of the PowerShell single-quoted literal; a literal `%%` in that
   path gets silently substituted by cmd.exe's `%VAR%` expansion, which
   can corrupt which file a batch command actually targets.
2. The detached update.bat ran with zero logging, so a failure during the
   actual file-swap left no trace anywhere.
3. Every failure branch that falls through to `:restore` left the
   already-downloaded QuickRes_new.exe orphaned on disk.

These tests verify: (a) a single quote in the reverify path is escaped so
it cannot close the PowerShell literal early; (b) a literal `%` in a path
is doubled before landing in the generated batch text; (c) the
`:restore`-reachable failure path deletes the leftover new-exe file; (d)
the generated script writes log lines at key steps/failure branches.
"""
import hashlib

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


def _generated_script(monkeypatch, exe_dir, exe_name="QuickRes.exe", log_path=None):
    exe_dir.mkdir(parents=True, exist_ok=True)
    fake_exe = exe_dir / exe_name
    fake_exe.write_bytes(b"old")
    monkeypatch.setattr(updater.sys, "executable", str(fake_exe))
    if log_path is not None:
        monkeypatch.setattr(updater, "LOG_PATH", str(log_path))

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

    bat_path = exe_dir / "update.bat"
    return bat_path.read_text()


class TestReverifyCommandEscapesSingleQuote:
    def test_build_reverify_command_doubles_embedded_single_quote(self):
        malicious = r"C:\Users\evil' ; Remove-Item C:\ -Recurse -Force #\QuickRes_new.exe"
        cmd = updater._build_reverify_command(malicious, None)

        assert "evil'' ;" in cmd
        assert "evil' ;" not in cmd

    def test_apply_update_reverify_line_escapes_single_quote_in_install_dir(
        self, monkeypatch, tmp_path
    ):
        # A single quote in an otherwise Windows-legal directory name (no
        # `:`, `\`, or other reserved filename characters) is enough to
        # exercise the real apply_update() -> _build_reverify_command()
        # path end-to-end.
        evil_dir = tmp_path / "evil' -and $true -or 'x"
        script = _generated_script(monkeypatch, evil_dir)

        ps_line = next(
            l for l in script.splitlines() if l.strip().lower().startswith("powershell")
        )
        assert "evil'' -and $true -or ''x" in ps_line
        assert "evil' -and" not in ps_line


class TestBatchTextEscapesLiteralPercent:
    def test_escape_batch_percent_doubles_percent_signs(self):
        assert updater._escape_batch_percent("50%off") == "50%%off"
        assert updater._escape_batch_percent("safe") == "safe"

    def test_apply_update_paths_containing_percent_are_doubled_in_batch_text(
        self, monkeypatch, tmp_path
    ):
        pct_dir = tmp_path / "50%off"
        script = _generated_script(monkeypatch, pct_dir)

        exe_path = str(pct_dir / "QuickRes.exe")
        # The raw (unescaped) path must never appear literally in the
        # generated batch text -- only the doubled-percent form may.
        assert exe_path not in script
        assert exe_path.replace("%", "%%") in script


class TestRestorePathCleansUpLeftoverNewExe:
    def test_restore_branch_deletes_leftover_new_exe(self, monkeypatch, tmp_path):
        script = _generated_script(monkeypatch, tmp_path)
        lines = [l.strip() for l in script.splitlines()]

        restore_idx = lines.index(":restore")
        new_exe_needle = str(tmp_path / "QuickRes_new.exe").lower()

        cleanup_lines = [
            l
            for l in lines[restore_idx:]
            if "del " in l.lower() and new_exe_needle in l.lower()
        ]
        assert cleanup_lines, "expected a cleanup del of the leftover new exe at :restore"

    def test_all_failure_branches_reach_restore_before_cleanup(self, monkeypatch, tmp_path):
        script = _generated_script(monkeypatch, tmp_path)
        lines = [l.strip() for l in script.splitlines()]

        # Every dedicated failure label (retries exhausted, reverify
        # rejected, move failed) must reach :restore -- either via an
        # explicit `goto :restore`, or (for the failure branch positioned
        # immediately before it) by falling straight through into it.
        for label in (":renfail", ":reverifyfail", ":movefail"):
            idx = lines.index(label)
            following = "\n".join(lines[idx : idx + 3]).lower()
            assert ":restore" in following


class TestBatchScriptWritesLogLines:
    def test_log_target_points_at_configured_log_path(self, monkeypatch, tmp_path):
        log_path = tmp_path / "logs" / "quickres.log"
        script = _generated_script(monkeypatch, tmp_path / "install", log_path=log_path)

        assert str(log_path) in script
        assert 'set "QR_LOG=' in script

    def test_echo_statements_redirect_to_the_log_variable(self, monkeypatch, tmp_path):
        script = _generated_script(monkeypatch, tmp_path)
        echo_lines = [l for l in script.splitlines() if l.strip().lower().startswith("echo")]

        assert echo_lines, "expected log-writing echo statements in the generated script"
        assert all('>>"%QR_LOG%"' in l for l in echo_lines)

    def test_log_lines_present_on_each_failure_branch_and_key_steps(
        self, monkeypatch, tmp_path
    ):
        script = _generated_script(monkeypatch, tmp_path)
        lines = [l.strip() for l in script.splitlines()]

        for label in (
            ":renfail",
            ":reverifyfail",
            ":movefail",
            ":renamed",
            ":launch",
            ":confirmed",
            ":cleanup",
        ):
            idx = lines.index(label)
            following = lines[idx + 1]
            assert following.lower().startswith("echo"), (
                f"expected a log line right after {label}"
            )


class TestReverifyCommandStillCarriesHashCheck:
    def test_sha256_check_still_present_alongside_escaping(self, monkeypatch, tmp_path):
        expected = hashlib.sha256(_valid_pe_payload()).hexdigest()

        exe_dir = tmp_path
        exe_dir.mkdir(parents=True, exist_ok=True)
        fake_exe = exe_dir / "QuickRes.exe"
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
            updater.apply_update(
                "https://lxzy.my/QuickRes_new.exe", version_info={"sha256": expected}
            )

        script = (exe_dir / "update.bat").read_text()
        assert "Get-FileHash" in script
        assert expected.upper() in script

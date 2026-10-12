"""Corrective fix for quickres/updater.py (_build_reverify_command):

`expected_sha256` -- the value read from the version-check response's
`sha256` field -- used to be interpolated into the generated PowerShell
reverify one-liner with `str(expected_sha256).upper()` and no escaping and
no format validation, even though it sits in the exact same two nested
quoting contexts (a PowerShell single-quoted literal, itself inside one
line of a cmd.exe-parsed batch script) that `new_exe_path` two lines above
was already deliberately escaped for via
`_escape_batch_percent(_escape_ps_single_quoted(...))`. A value containing a
single quote could break out of the PowerShell string literal; a value
containing a percent sign would trigger cmd.exe's own `%VAR%` substitution.
This field is currently dormant (the live server does not supply it yet)
but is otherwise entirely attacker/server controlled JSON.

The fix applies the identical escaping treatment already used for
`new_exe_path`, and additionally validates the value is exactly 64
hexadecimal characters (the only legal shape for a SHA-256 hex digest)
before ever interpolating it -- a malformed value is logged and the hash
check is skipped, the same way a missing value already is, rather than
trusting it after only escaping it.
"""
import re

from quickres import updater


class TestMalformedSha256IsSkippedNotInterpolated:
    def test_injection_chars_result_in_no_hash_check_at_all(self):
        malicious = "AAAA'; Remove-Item -Recurse -Force C:\\Users; '%PATH%"
        cmd = updater._build_reverify_command(
            r"C:\dir\QuickRes_new.exe", malicious
        )

        # Not a valid 64-hex-character sha256, so the hash check must be
        # skipped entirely -- neither the raw nor the escaped/uppercased
        # form of the malicious value may appear anywhere in the generated
        # PowerShell command.
        assert "Get-FileHash" not in cmd
        assert malicious not in cmd
        assert malicious.upper() not in cmd
        assert "Remove-Item" not in cmd

    def test_wrong_length_hex_value_is_also_skipped(self):
        # Valid hex characters, but not exactly 64 of them.
        too_short = "AB" * 10
        cmd = updater._build_reverify_command(
            r"C:\dir\QuickRes_new.exe", too_short
        )

        assert "Get-FileHash" not in cmd
        assert too_short.upper() not in cmd

    def test_malformed_value_logs_a_skip_message(self, monkeypatch):
        logged = []
        monkeypatch.setattr(updater, "log_msg", lambda msg: logged.append(msg))

        updater._build_reverify_command(
            r"C:\dir\QuickRes_new.exe", "not-a-real-hash"
        )

        assert any("sha256" in m.lower() for m in logged)


class TestValidSha256StillPassesThrough:
    def test_valid_64_hex_char_value_is_included_and_uppercased(self):
        valid = "a" * 64
        cmd = updater._build_reverify_command(
            r"C:\dir\QuickRes_new.exe", valid
        )

        assert "Get-FileHash" in cmd
        assert valid.upper() in cmd

    def test_no_expected_hash_still_omits_hash_check(self):
        cmd = updater._build_reverify_command(r"C:\dir\QuickRes_new.exe", None)

        assert "Get-FileHash" not in cmd


class TestSha256GetsSameEscapingTreatmentAsNewExePath:
    """Direct proof that the escaping call chain applied to expected_sha256
    matches the pattern already used for new_exe_path -- exercised with the
    64-character format check temporarily widened to accept anything, so
    the escaping itself (not just the format gate) is what is being
    verified here.
    """

    def test_embedded_quote_and_percent_are_doubled_not_left_raw(
        self, monkeypatch
    ):
        monkeypatch.setattr(updater, "_SHA256_HEX_RE", re.compile(r".*", re.DOTALL))
        # Already upper-case: _build_reverify_command uppercases
        # expected_sha256 before escaping/interpolating it, so a mixed-case
        # input would not round-trip identically through this assertion.
        malicious = "AAAA' ; REMOVE-ITEM -RECURSE -FORCE C:\\USERS ; '50%OFF"

        cmd = updater._build_reverify_command(
            r"C:\dir\QuickRes_new.exe", malicious
        )

        assert "Get-FileHash" in cmd
        # Raw (unescaped) form must never appear.
        assert "AAAA' ; REMOVE-ITEM" not in cmd
        assert "50%OFF" not in cmd
        # Escaped form (doubled quote, doubled percent) must appear instead.
        assert "AAAA'' ; REMOVE-ITEM" in cmd
        assert "50%%OFF" in cmd

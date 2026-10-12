"""Stream D (round 3) -- PowerShell injection primitive in
launch_appx_app()/launch_start_app().

Both functions build `powershell -Command <string>` invocations by
f-string-interpolating caller-supplied values (name_like, publisher_like,
substring) directly into single-quoted PowerShell string literals
(`-like '*{value}*'`). PowerShell single-quoted strings are fully literal
except for the `''` (doubled single quote) escape sequence used to embed a
literal single quote -- so an unescaped `'` in the interpolated value closes
the quoted literal early and lets anything after it execute as PowerShell
code. These tests assert that a value containing a single quote is escaped
(quote doubled) before landing in the generated command string, so it can
never break out of its quoted context.
"""

from quickres import display


class _FakeCompleted:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""


def test_escape_ps_single_quoted_doubles_embedded_quotes():
    assert display._escape_ps_single_quoted("O'Brien") == "O''Brien"


def test_escape_ps_single_quoted_leaves_safe_text_untouched():
    assert display._escape_ps_single_quoted("Intel Graphics") == "Intel Graphics"


def test_launch_appx_app_escapes_single_quote_in_name_like(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout="")

    monkeypatch.setattr(display.subprocess, "run", fake_run)

    malicious = "x' ; Remove-Item C:\\ -Recurse -Force #"
    display.launch_appx_app(malicious)

    ps_cmd = captured["cmd"][-1]
    # The escaped payload (quote doubled) must appear verbatim...
    assert "x'' ; Remove-Item C:\\ -Recurse -Force #" in ps_cmd
    # ...and the raw single-quote-then-space break-out sequence must NOT
    # appear anywhere in the generated command (it would only appear if the
    # quote were left unescaped, closing the '*...*' literal early).
    assert "x' ;" not in ps_cmd


def test_launch_appx_app_escapes_single_quote_in_publisher_like(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout="")

    monkeypatch.setattr(display.subprocess, "run", fake_run)

    malicious_publisher = "Evil' -and $true -or '1"
    display.launch_appx_app("Graphics", malicious_publisher)

    ps_cmd = captured["cmd"][-1]
    assert "Evil'' -and $true -or ''1" in ps_cmd
    assert "Evil' -and" not in ps_cmd


def test_launch_start_app_escapes_single_quote_in_substring(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted(stdout="")

    monkeypatch.setattr(display.subprocess, "run", fake_run)

    malicious = "x' } ; Remove-Item C:\\ -Recurse -Force ; Where-Object { $_.Name -like 'y"
    display.launch_start_app(malicious)

    ps_cmd = captured["cmd"][-1]
    assert "x'' } ; Remove-Item C:\\ -Recurse -Force ; Where-Object { $_.Name -like ''y" in ps_cmd
    assert "x' }" not in ps_cmd

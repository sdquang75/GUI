"""1c: updater.apply_update must validate download_url's scheme + host
against an explicit allowlist before ever opening it, mirroring bridge.py's
open_external discipline. UPDATE_URL (lxzy.my/version.json) is the
version-CHECK endpoint; the real download_url comes from that endpoint's
JSON response, so the allowlist covers the project's actual release hosts:
lxzy.my (same domain as the version-check endpoint) and github.com (GitHub
Releases is a common asset host for this kind of app).

RISK FLAGGED FOR USER CONFIRMATION: the exact intended download host was
genuinely ambiguous from the repo alone (no release asset URL is checked
into the repo) -- this allowlist is the most defensible default, not a
verified fact.
"""
import pytest

from quickres import updater


class TestValidateDownloadUrl:
    def test_rejects_non_https_scheme(self):
        with pytest.raises(ValueError):
            updater._validate_download_url("http://lxzy.my/QuickRes.exe")

    def test_rejects_non_allowlisted_host(self):
        with pytest.raises(ValueError):
            updater._validate_download_url("https://evil.example.com/QuickRes.exe")

    def test_accepts_lxzy_my_with_any_path(self):
        updater._validate_download_url("https://lxzy.my/QuickRes.exe")

    def test_accepts_github_url_under_this_projects_own_repo(self):
        updater._validate_download_url(
            "https://github.com/lxzydev/QuickRes/releases/download/v1.0.7/QuickRes.exe"
        )

    def test_rejects_github_url_under_a_different_repo(self):
        # Round 24 finding: the allowlist previously accepted ANY
        # github.com host+path, so a trusted-but-compromised version-check
        # response could point download_url at an attacker-controlled
        # release asset in someone else's repo and it would still pass.
        # The path must now be scoped to this project's actual repository
        # (https://github.com/lxzydev/QuickRes, per webview/bridge.py's own
        # open_external allowlist comment naming the same identity).
        with pytest.raises(ValueError):
            updater._validate_download_url(
                "https://github.com/attacker/evil-repo/releases/download/v1/QuickRes.exe"
            )

    def test_rejects_github_url_with_no_path_at_all(self):
        with pytest.raises(ValueError):
            updater._validate_download_url("https://github.com/QuickRes.exe")


class TestApplyUpdateEnforcesAllowlistBeforeDownload:
    def test_rejects_non_https_scheme_before_urlopen(self, monkeypatch):
        calls = []
        monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: calls.append(1))

        with pytest.raises(ValueError):
            updater.apply_update("http://lxzy.my/QuickRes_new.exe")

        assert calls == []

    def test_rejects_non_allowlisted_host_before_urlopen(self, monkeypatch):
        calls = []
        monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: calls.append(1))

        with pytest.raises(ValueError):
            updater.apply_update("https://evil.example.com/QuickRes_new.exe")

        assert calls == []

    def test_allowlisted_host_proceeds_to_download(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))

        urlopen_calls = []

        # Round 2 addition: apply_update now verifies the downloaded bytes
        # look like a plausible Windows PE executable (see
        # test_updater_integrity_and_rollback.py) before staging, so this
        # allowlist-focused fixture must return a minimal valid DOS/PE
        # header rather than arbitrary bytes.
        _header = bytearray(64)
        _header[0:2] = b"MZ"
        _header[60:64] = (64).to_bytes(4, "little")
        _valid_pe_payload = bytes(_header) + b"PE\x00\x00" + b"restofheader"

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return _valid_pe_payload

        # Round 3: apply_update now downloads via a dedicated opener
        # (urllib.request.build_opener(_AllowlistRedirectHandler())) rather
        # than the bare module-level urlopen(), so the redirect target can
        # be re-validated before it is followed. Mock at that integration
        # point instead.
        class _FakeOpener:
            def open(self, request, timeout=None):
                urlopen_calls.append(1)
                return _FakeResp()

        monkeypatch.setattr(
            updater.urllib.request, "build_opener", lambda *h: _FakeOpener()
        )
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        with pytest.raises(SystemExit):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        assert urlopen_calls == [1]


class TestAllowlistRedirectHandler:
    """Round 3 finding: _validate_download_url only ever checked the
    INITIAL download_url. urllib's default opener transparently follows
    301/302/303/307 redirects to ANY host with zero re-validation, so a
    real GitHub Releases asset URL (github.com, allowlisted) redirecting to
    objects.githubusercontent.com (NOT allowlisted) bypassed the allowlist
    entirely once a redirect happened. Fix: a custom
    HTTPRedirectHandler subclass that validates the redirect target BEFORE
    building the follow-up request, not after the fact via resp.geturl().
    """

    def test_rejects_redirect_to_non_allowlisted_host_before_following(
        self, monkeypatch
    ):
        # Track whether the base class's redirect_request (the step that
        # actually builds/would-open the follow-up request) is ever
        # reached. It must NOT be reached for a non-allowlisted target.
        base_calls = []
        monkeypatch.setattr(
            updater.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            lambda self, *a, **k: base_calls.append(1),
        )
        handler = updater._AllowlistRedirectHandler()
        req = updater.urllib.request.Request(
            "https://github.com/owner/repo/releases/download/v1/QuickRes.exe"
        )

        with pytest.raises(ValueError):
            handler.redirect_request(
                req,
                None,
                302,
                "Found",
                {},
                "https://objects.githubusercontent.com/evil-swap/QuickRes.exe",
            )

        assert base_calls == []

    def test_allows_redirect_to_allowlisted_host(self, monkeypatch):
        base_calls = []
        monkeypatch.setattr(
            updater.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            lambda self, *a, **k: base_calls.append(1) or "built-request",
        )
        handler = updater._AllowlistRedirectHandler()
        req = updater.urllib.request.Request("https://lxzy.my/QuickRes_new.exe")

        result = handler.redirect_request(
            req, None, 302, "Found", {}, "https://lxzy.my/moved/QuickRes_new.exe"
        )

        assert base_calls == [1]
        assert result == "built-request"


class TestApplyUpdateUsesRedirectValidatingOpener:
    def test_apply_update_installs_allowlist_redirect_handler(
        self, monkeypatch, tmp_path
    ):
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

        build_opener_calls = []

        class _FakeOpener:
            def open(self, request, timeout=None):
                assert timeout == 30
                return _FakeResp()

        def _fake_build_opener(*handlers):
            build_opener_calls.append(handlers)
            return _FakeOpener()

        monkeypatch.setattr(
            updater.urllib.request, "build_opener", _fake_build_opener
        )
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

        with pytest.raises(SystemExit):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        assert len(build_opener_calls) == 1
        assert any(
            isinstance(h, updater._AllowlistRedirectHandler)
            for h in build_opener_calls[0]
        )

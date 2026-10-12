"""Round 4 corrective fixes for quickres/updater.py (Stream B):

1. HIGH (acknowledged design gap): no cryptographic integrity verification
   existed beyond the structural PE-header check (round 3) -- any
   attacker-built PE with valid headers would pass. Full code-signing (a
   signing certificate + signed release pipeline) is out of scope for a
   code-only session. This adds a pragmatic, partial, CLIENT-SIDE capability
   instead: when the version-check JSON response (fetch_version_info()'s
   return value) includes an expected "sha256" field, apply_update()/
   confirm_update() compute the downloaded file's SHA-256 (chunked read) and
   refuse to proceed -- fail closed, cleaning up the bad download -- on a
   mismatch. When the field is absent (true of the CURRENT server response,
   until a maintainer adds it server-side), the hash check is skipped
   gracefully and behavior is unchanged, so this is fully backward
   compatible today.

2. LOW: fetch_version_info() used the bare default urlopen() opener, which
   follows redirects to ANY host unvalidated -- unlike apply_update()'s
   download path (round 3), which re-validates every redirect target
   against the host allowlist via _AllowlistRedirectHandler before following
   it. Fixed for consistency: fetch_version_info() now uses the same
   build_opener(_AllowlistRedirectHandler()).open(...) pattern.
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


def _patch_download(monkeypatch, payload):
    class _FakeOpener:
        def open(self, request, timeout=None):
            return _FakeResp(payload)

    monkeypatch.setattr(
        updater.urllib.request, "build_opener", lambda *h: _FakeOpener()
    )


class TestApplyUpdateHashVerification:
    def _setup(self, monkeypatch, tmp_path, payload):
        fake_exe = tmp_path / "QuickRes.exe"
        fake_exe.write_bytes(b"old")
        monkeypatch.setattr(updater.sys, "executable", str(fake_exe))
        _patch_download(monkeypatch, payload)
        monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

    def test_mismatched_hash_rejected_and_cleaned_up(self, monkeypatch, tmp_path):
        payload = _valid_pe_payload()
        self._setup(monkeypatch, tmp_path, payload)
        version_info = {"sha256": "0" * 64}

        with pytest.raises(ValueError, match="(?i)sha-?256"):
            updater.apply_update(
                "https://lxzy.my/QuickRes_new.exe", version_info=version_info
            )

        assert not (tmp_path / "QuickRes_new.exe").exists()
        assert not (tmp_path / "update.bat").exists()

    def test_missing_hash_field_proceeds_normally(self, monkeypatch, tmp_path):
        payload = _valid_pe_payload()
        self._setup(monkeypatch, tmp_path, payload)

        with pytest.raises(SystemExit):
            updater.apply_update(
                "https://lxzy.my/QuickRes_new.exe",
                version_info={"version": "1.2.3"},
            )

        assert (tmp_path / "update.bat").exists()

    def test_no_version_info_at_all_proceeds_normally(self, monkeypatch, tmp_path):
        payload = _valid_pe_payload()
        self._setup(monkeypatch, tmp_path, payload)

        with pytest.raises(SystemExit):
            updater.apply_update("https://lxzy.my/QuickRes_new.exe")

        assert (tmp_path / "update.bat").exists()

    def test_matching_hash_passes(self, monkeypatch, tmp_path):
        payload = _valid_pe_payload()
        self._setup(monkeypatch, tmp_path, payload)
        expected = hashlib.sha256(payload).hexdigest()

        with pytest.raises(SystemExit):
            updater.apply_update(
                "https://lxzy.my/QuickRes_new.exe",
                version_info={"sha256": expected},
            )

        assert (tmp_path / "update.bat").exists()

    def test_matching_hash_is_case_insensitive(self, monkeypatch, tmp_path):
        payload = _valid_pe_payload()
        self._setup(monkeypatch, tmp_path, payload)
        expected = hashlib.sha256(payload).hexdigest().upper()

        with pytest.raises(SystemExit):
            updater.apply_update(
                "https://lxzy.my/QuickRes_new.exe",
                version_info={"sha256": expected},
            )

        assert (tmp_path / "update.bat").exists()


class TestConfirmUpdatePassesVersionInfoThrough:
    def test_confirm_update_forwards_version_info_to_apply_update(self, monkeypatch):
        captured = {}

        def fake_apply_update(download_url, version_info=None):
            captured["download_url"] = download_url
            captured["version_info"] = version_info
            raise SystemExit(0)

        monkeypatch.setattr(updater, "apply_update", fake_apply_update)
        monkeypatch.setattr(updater.os, "_exit", lambda code: None)

        updater.confirm_update(
            "https://lxzy.my/x.exe", version_info={"sha256": "abc"}
        )

        assert captured == {
            "download_url": "https://lxzy.my/x.exe",
            "version_info": {"sha256": "abc"},
        }

    def test_confirm_update_still_works_without_version_info(self, monkeypatch):
        captured = {}

        def fake_apply_update(download_url, version_info=None):
            captured["version_info"] = version_info
            raise SystemExit(0)

        monkeypatch.setattr(updater, "apply_update", fake_apply_update)
        monkeypatch.setattr(updater.os, "_exit", lambda code: None)

        updater.confirm_update("https://lxzy.my/x.exe")

        assert captured == {"version_info": None}


class TestFetchVersionInfoUsesAllowlistRedirectOpener:
    def test_fetch_version_info_installs_allowlist_redirect_handler(
        self, monkeypatch
    ):
        build_opener_calls = []

        class _FakeOpener:
            def open(self, request, timeout=None):
                assert timeout == 5
                return _FakeResp(b'{"version": "1.0"}')

        def _fake_build_opener(*handlers):
            build_opener_calls.append(handlers)
            return _FakeOpener()

        monkeypatch.setattr(
            updater.urllib.request, "build_opener", _fake_build_opener
        )

        result = updater.fetch_version_info()

        assert result == {"version": "1.0"}
        assert len(build_opener_calls) == 1
        assert any(
            isinstance(h, updater._AllowlistRedirectHandler)
            for h in build_opener_calls[0]
        )

    def test_fetch_version_info_no_longer_uses_bare_urlopen(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            updater.urllib.request, "urlopen", lambda *a, **k: calls.append(1)
        )

        class _FakeOpener:
            def open(self, request, timeout=None):
                return _FakeResp(b"{}")

        monkeypatch.setattr(
            updater.urllib.request, "build_opener", lambda *h: _FakeOpener()
        )

        updater.fetch_version_info()

        assert calls == []

"""Round 14 R2 Readability finding for quickres/updater.py.

`apply_update` builds its entire update.bat state machine (labels like
`:renwait`, `:renfail`, `:renamed`, ... plus `goto` chains between them) as
one large string-concatenated literal. Nothing previously verified that
every `goto <label>` in that generated script actually resolves to a
matching `:label` definition in the same script -- a typo'd label name
introduced by a future edit would only surface at runtime, during an
actual failed update, not via pytest or any linter.

These tests add a small, generic label-graph validator
(`updater._extract_batch_label_graph` / `updater._validate_batch_label_graph`)
and exercise it two ways:

1. Against a synthetic, deliberately-broken script, to prove the validator
   actually detects a dangling `goto` target (and doesn't just pass
   trivially).
2. Against the REAL `bat_contents` produced by a live `apply_update` call,
   so a future typo'd goto/label in that generation code would fail this
   test immediately instead of waiting for a real broken update.
"""
import pytest

from quickres import updater


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def _valid_pe_payload():
    header = bytearray(64)
    header[0:2] = b"MZ"
    header[60:64] = (64).to_bytes(4, "little")
    return bytes(header) + b"PE\x00\x00" + b"restofheader"


def _real_generated_script(monkeypatch, exe_dir):
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
        updater.apply_update("https://lxzy.my/QuickRes_new.exe")

    bat_path = exe_dir / "update.bat"
    return bat_path.read_text()


class TestExtractBatchLabelGraph:
    def test_finds_defined_labels_and_goto_targets(self):
        script = (
            "@echo off\n"
            "goto :start\n"
            ":start\n"
            "if errorlevel 1 goto :done\n"
            ":done\n"
            "echo finished\n"
        )
        defined, referenced = updater._extract_batch_label_graph(script)

        assert defined == {"start", "done"}
        assert referenced == {"start", "done"}

    def test_goto_matching_is_case_insensitive(self):
        script = "GOTO :Start\n:START\necho hi\n"
        defined, referenced = updater._extract_batch_label_graph(script)

        assert defined == {"start"}
        assert referenced == {"start"}


class TestValidateBatchLabelGraphDetectsDanglingGoto:
    def test_raises_on_a_goto_target_with_no_matching_label(self):
        broken_script = (
            "@echo off\n"
            ":renwait\n"
            "if not errorlevel 1 goto :renamed\n"  # typo'd target: no
            "goto :renwait\n"  # ":renamed" label defined anywhere below
            ":renfail\n"
            "echo done\n"
        )

        with pytest.raises(AssertionError, match="renamed"):
            updater._validate_batch_label_graph(broken_script)

    def test_passes_on_a_well_formed_script(self):
        good_script = (
            "@echo off\n"
            "goto :a\n"
            ":a\n"
            "goto :b\n"
            ":b\n"
            "echo ok\n"
        )
        # Must not raise.
        updater._validate_batch_label_graph(good_script)


class TestRealGeneratedUpdateBatScriptHasConsistentLabelGraph:
    def test_apply_update_bat_contents_goto_targets_all_resolve(
        self, monkeypatch, tmp_path
    ):
        script = _real_generated_script(monkeypatch, tmp_path)

        # Must not raise -- every `goto :label` in the actual generated
        # update.bat must resolve to a matching `:label` definition
        # somewhere in that same script.
        updater._validate_batch_label_graph(script)

"""Round 13 corrective fix for quickres/updater.py (Resilience):

The generated update.bat's very first real step unconditionally deleted any
pre-existing "<exe>.old" backup, before the new download/PE-check/reverify
sequence had proven anything about the CURRENT update attempt. apply_update's
own comments (see the NOTE above `reverify_cmd` in apply_update) establish
that when a prior update's post-move launch was never confirmed, "<exe>.old"
is deliberately LEFT on disk as a manual-rollback path. But the unconditional
`del` at the top of the next generated script discarded that backup
regardless of whether the new attempt ultimately succeeded, silently
destroying the one documented manual-rollback path this codebase provides.

Fix: the pre-existing backup is renamed out of the way (to a distinct
"<exe>.old.prev" name) instead of deleted, so the canonical "<exe>.old" name
stays free for THIS attempt's own backup (needed for `ren` to succeed) without
destroying the prior one. The prior backup is only deleted once this attempt
reaches `:confirmed` (a subsequent update proven to actually launch); if this
attempt fails validation/launch-confirmation, `:restore` puts the prior
backup back under the canonical "<exe>.old" name so it remains available,
and the unconfirmed `:cleanup` path leaves it untouched under its "prev"
name.
"""
from quickres import updater


def _valid_pe_payload():
    header = bytearray(64)
    header[0:2] = b"MZ"
    header[60:64] = (64).to_bytes(4, "little")
    return bytes(header) + b"PE\x00\x00" + b"restofheader"


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return _valid_pe_payload()


def _generated_script(monkeypatch, tmp_path, preexisting_backup_contents=b"prior-backup"):
    fake_exe = tmp_path / "QuickRes.exe"
    fake_exe.write_bytes(b"old")
    monkeypatch.setattr(updater.sys, "executable", str(fake_exe))

    if preexisting_backup_contents is not None:
        (tmp_path / "QuickRes.exe.old").write_bytes(preexisting_backup_contents)

    class _FakeOpener:
        def open(self, request, timeout=None):
            return _FakeResp()

    monkeypatch.setattr(
        updater.urllib.request, "build_opener", lambda *h: _FakeOpener()
    )
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

    import pytest

    with pytest.raises(SystemExit):
        updater.apply_update("https://lxzy.my/QuickRes_new.exe")

    bat_path = tmp_path / "update.bat"
    return bat_path.read_text()


def _label_index(lines, label):
    return next(i for i, line in enumerate(lines) if line.strip().lower() == label.lower())


def _is_del_of(line, needle):
    """True if `line` (already lowercased) issues a `del "<needle>"` command
    anywhere on it -- lines are frequently gated with a leading
    `if exist "..." ` guard, so a plain `startswith("del ")` would miss
    those.
    """
    return f'del "{needle}"' in line


def _is_ren_of(line, needle):
    """True if `line` (already lowercased) issues a `ren "<needle>" ...`
    command anywhere on it (see `_is_del_of` for why not `startswith`)."""
    return f'ren "{needle}"' in line


class TestPreexistingBackupSurvivesUnconfirmedAttempt:
    """(a) old backup survives when the new update's launch is never confirmed."""

    def test_top_of_script_does_not_unconditionally_delete_preexisting_backup(
        self, monkeypatch, tmp_path
    ):
        script = _generated_script(monkeypatch, tmp_path)
        lines = script.splitlines()

        renwait_idx = _label_index(lines, ":renwait")
        old_backup_needle = str(tmp_path / "QuickRes.exe.old").lower()

        preamble = [l.strip().lower() for l in lines[:renwait_idx]]
        # Nothing before the first rename attempt may unconditionally `del`
        # the pre-existing ".old" backup.
        assert not any(_is_del_of(line, old_backup_needle) for line in preamble)

    def test_preexisting_backup_is_moved_to_a_distinct_prev_name(
        self, monkeypatch, tmp_path
    ):
        script = _generated_script(monkeypatch, tmp_path)
        lines = script.splitlines()

        renwait_idx = _label_index(lines, ":renwait")
        old_backup_needle = str(tmp_path / "QuickRes.exe.old").lower()
        prev_backup_needle = str(tmp_path / "QuickRes.exe.old.prev").lower()

        preamble = [l.strip().lower() for l in lines[:renwait_idx]]
        rename_lines = [line for line in preamble if _is_ren_of(line, old_backup_needle)]
        assert rename_lines, "expected the pre-existing backup to be renamed, not deleted"
        assert any(prev_backup_needle.split("\\")[-1] in line for line in rename_lines)

    def test_prev_backup_not_deleted_before_confirmed_label(self, monkeypatch, tmp_path):
        script = _generated_script(monkeypatch, tmp_path)
        lines = script.splitlines()

        confirmed_idx = _label_index(lines, ":confirmed")
        prev_backup_needle = str(tmp_path / "QuickRes.exe.old.prev").lower()

        before_confirmed = [l.strip().lower() for l in lines[:confirmed_idx]]
        assert not any(_is_del_of(line, prev_backup_needle) for line in before_confirmed)

    def test_restore_branch_restores_prev_backup_to_canonical_name(
        self, monkeypatch, tmp_path
    ):
        script = _generated_script(monkeypatch, tmp_path)
        lines = script.splitlines()

        restore_idx = _label_index(lines, ":restore")
        launch_idx = _label_index(lines, ":launch")
        old_backup_needle = str(tmp_path / "QuickRes.exe.old").lower()
        prev_backup_needle = str(tmp_path / "QuickRes.exe.old.prev").lower()

        restore_block = [l.strip().lower() for l in lines[restore_idx:launch_idx]]
        restore_prev_lines = [
            line for line in restore_block if _is_ren_of(line, prev_backup_needle)
        ]
        assert restore_prev_lines, "expected :restore to rename the prev backup back"
        # It must be renamed back to the plain ".old" name, not deleted.
        assert any(line.endswith('.old"') or line.endswith(".old") for line in restore_prev_lines)
        assert not any(_is_del_of(line, prev_backup_needle) for line in restore_block)


class TestPrevBackupEventuallyCleanedUpOnConfirmedSuccess:
    """(b) old backup IS eventually cleaned up once a subsequent update is
    confirmed as successfully running."""

    def test_confirmed_branch_deletes_prev_backup(self, monkeypatch, tmp_path):
        script = _generated_script(monkeypatch, tmp_path)
        lines = script.splitlines()

        confirmed_idx = _label_index(lines, ":confirmed")
        cleanup_idx = _label_index(lines, ":cleanup")
        prev_backup_needle = str(tmp_path / "QuickRes.exe.old.prev").lower()

        confirmed_block = [l.strip().lower() for l in lines[confirmed_idx:cleanup_idx]]
        assert any(_is_del_of(line, prev_backup_needle) for line in confirmed_block)

    def test_confirmed_branch_still_deletes_current_attempts_own_backup(
        self, monkeypatch, tmp_path
    ):
        # Existing (round 2) behavior must be preserved alongside the new
        # prev-backup cleanup.
        script = _generated_script(monkeypatch, tmp_path)
        lines = script.splitlines()

        confirmed_idx = _label_index(lines, ":confirmed")
        cleanup_idx = _label_index(lines, ":cleanup")
        old_backup_needle = str(tmp_path / "QuickRes.exe.old").lower()

        confirmed_block = [l.strip().lower() for l in lines[confirmed_idx:cleanup_idx]]
        assert any(_is_del_of(line, old_backup_needle) for line in confirmed_block)


class TestNoPreexistingBackupStillWorks:
    def test_script_generation_unaffected_when_no_preexisting_backup(
        self, monkeypatch, tmp_path
    ):
        # Must not error/crash when there is nothing to preserve.
        script = _generated_script(
            monkeypatch, tmp_path, preexisting_backup_contents=None
        )
        assert ":renwait" in script
        assert ":confirmed" in script

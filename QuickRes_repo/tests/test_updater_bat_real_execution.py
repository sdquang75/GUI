"""Round 15 corrective fix for quickres/updater.py (Readability finding):

Every existing updater test (test_updater_batch_label_graph.py,
test_updater_backup_chain_preservation.py, test_updater_batch_and_ps_escaping.py,
test_updater_toctou_reverify.py, test_updater_integrity_and_rollback.py)
monkeypatches `subprocess.Popen` to a no-op before calling `apply_update`, so
the generated `update.bat` text is checked STATICALLY (label graph,
substring/ordering assertions on the raw text) but is never actually
executed by `cmd.exe` in any test. `_validate_batch_label_graph` only proves
every `goto` target resolves to *some* label -- it says nothing about
whether the command text inside a branch is actually correct. A typo in the
real command text of one branch would only ever surface as a real failed
update on a user's machine, not as a test failure.

This module closes that gap: it captures the exact `bat_contents`
`apply_update` generates (with `subprocess.Popen` mocked for that one
generation step only, matching the existing test-suite pattern, then
immediately un-mocked), writes it to a REAL `update.bat` in a temp
directory, and executes it with a real, unmocked `cmd.exe /c` subprocess
against real stand-in files -- then asserts on the real, observable
filesystem outcome rather than on the script's text.

Two real system executables (`hostname.exe`, `whoami.exe`) stand in for the
old/staged exe. Both are genuine, fast-exiting Windows PE binaries, so the
`:launch` step's real `start` call always targets a structurally valid
executable and can never trigger a blocking "this app can't run on your
PC"-style dialog; they exit well before the script's `timeout /t 2` +
`tasklist` check ever gets to look for the launched process, so no spawned
process is ever still alive by the time the batch script (and this test)
finish -- covering the "clean up any spawned processes" requirement without
actually needing to hunt one down.
"""
import os
import subprocess

import pytest

from quickres import updater

_HOSTNAME_EXE = r"C:\Windows\System32\hostname.exe"
_WHOAMI_EXE = r"C:\Windows\System32\whoami.exe"
# choice.exe (run with no arguments) sits at a Y/N prompt waiting on stdin
# instead of exiting immediately, so it is still present in `tasklist`
# several seconds after `start` launches it -- unlike hostname.exe/
# whoami.exe below, which complete and exit within milliseconds. This test
# module needs both behaviors as real, unmocked stand-ins: choice.exe proves
# a launch that genuinely stays running gets recognized and confirmed;
# hostname.exe/whoami.exe (reused below, unmodified, as a real staged
# update) prove a build that is a structurally valid PE but never shows up
# as a running process is treated as a launch failure and rolled back.
_CHOICE_EXE = r"C:\Windows\System32\choice.exe"


def _windows_only_env():
    """A real `cmd.exe /c update.bat` run must resolve `timeout`, `find`,
    `tasklist`, `powershell`, etc. to the genuine Windows System32 builds --
    the same ones the real, shipped app would resolve them to when its own
    `subprocess.Popen(["cmd", "/c", bat_path])` launches this script. When
    this test suite itself runs under a POSIX-flavored shell on Windows
    (e.g. Git Bash), that shell's own `PATH` can put GNU coreutils
    lookalikes (its own `timeout`/`find`) ahead of `System32`, which the
    real script's batch syntax (`timeout /t 2`, `find /i "..."`) is not
    written for and which would silently misbehave here in a way the real,
    normally-launched app never would. Build a minimal, unambiguous
    Windows-native `PATH` for the child process instead of trusting
    whatever inherited `PATH` this test process happens to have.
    """
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    system32 = os.path.join(windir, "System32")
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(
        [
            system32,
            windir,
            os.path.join(system32, "Wbem"),
            os.path.join(system32, "WindowsPowerShell", "v1.0"),
        ]
    )
    # The generated script's `:launch` step runs `start "" "QuickRes.exe"`
    # against a real system binary renamed to that filename in a temp
    # directory. Windows' Application Compatibility installer-detection
    # heuristic can flag that launch and trigger a real UAC elevation
    # prompt even though the target never asks for elevation itself.
    # __COMPAT_LAYER=RunAsInvoker opts the whole child process tree out of
    # that heuristic, matching this test's actual intent (a plain,
    # non-elevated relaunch).
    env["__COMPAT_LAYER"] = "RunAsInvoker"
    return env


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def _generate_real_bat(monkeypatch, tmp_path, download_payload, old_exe_bytes):
    """Drive the real `apply_update` to obtain the exact `bat_contents` (and
    on-disk layout) it would produce for a realistic install directory
    rooted at `tmp_path`, reusing the existing test-suite's mocked-download
    pattern for this generation step only.

    `subprocess.Popen` is mocked here (matching every other updater test) so
    generation itself doesn't try to launch anything -- but the mock is
    undone (`monkeypatch.undo()`) before returning, so the caller's own,
    later, real `subprocess.run` of the generated `update.bat` is NOT
    intercepted by it.
    """
    exe_path = tmp_path / "QuickRes.exe"
    exe_path.write_bytes(old_exe_bytes)
    monkeypatch.setattr(updater.sys, "executable", str(exe_path))
    monkeypatch.setattr(updater, "LOG_PATH", str(tmp_path / "quickres.log"))

    class _FakeOpener:
        def open(self, request, timeout=None):
            return _FakeResp(download_payload)

    monkeypatch.setattr(updater.urllib.request, "build_opener", lambda *h: _FakeOpener())
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)

    with pytest.raises(SystemExit):
        updater.apply_update("https://lxzy.my/QuickRes_new.exe")

    paths = {
        "exe_path": exe_path,
        "old_backup_path": tmp_path / "QuickRes.exe.old",
        "new_exe_path": tmp_path / "QuickRes_new.exe",
        "log_path": tmp_path / "quickres.log",
        "bat_path": tmp_path / "update.bat",
    }

    # Restore the real subprocess.Popen (and sys.executable/LOG_PATH) NOW --
    # only the generation step above should ever be mocked. The real
    # execution below must go through the genuine, unmocked cmd.exe.
    monkeypatch.undo()

    return paths


class _BatRunResult:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run_real_bat(bat_path, cwd):
    """Actually execute the generated script with a real, unmocked cmd.exe
    subprocess -- the whole point of this module: the batch text's real
    branching/command semantics get exercised by the real interpreter, not
    just statically parsed.
    """
    stdout_log = cwd / "_bat_stdout.log"
    stderr_log = cwd / "_bat_stderr.log"
    # Redirect the script's stdout/stderr to real files INSIDE the cmd.exe
    # command line (`>`/`2>`) rather than via Python-level `stdout=PIPE`/
    # `stderr=PIPE`. Requesting either of those from `subprocess.run` also
    # forces Python to explicitly hand the child an inherited (non-console)
    # stdin handle -- which defeats the whole point of the real console
    # `creationflags` below gives the child so its real `TIMEOUT.EXE` steps
    # (used throughout the generated script) can run at all: TIMEOUT
    # refuses to run ("Input redirection is not supported") the moment its
    # stdin isn't a genuine console, `/NOBREAK` notwithstanding.
    # A plain string (not an argv list) is passed as `args`: on Windows,
    # `subprocess.run` hands a string straight to `CreateProcess` as the
    # literal command line, bypassing `list2cmdline`'s own quoting -- which
    # would otherwise wrap this already-quoted redirection syntax in an
    # extra, mismatched layer of quotes and break cmd.exe's parsing of it.
    #
    # `call` is required immediately after `/c`: cmd.exe's own documented
    # `/c` quote-stripping rule strips exactly the FIRST and LAST quote
    # character of the entire remainder of the command line whenever that
    # remainder starts with a quote and does not consist of exactly one
    # quoted token -- which is exactly this command line's shape once the
    # `>"..." 2>"..."` redirection follows the quoted bat path. That silently
    # mangles both the quoting around the bat path AND the final
    # redirection target. Leading with the bare word `call` means the
    # remainder no longer starts with a quote, so that stripping rule never
    # triggers and every quoted argument is parsed as written.
    full_command_line = f'cmd /c call "{bat_path}" >"{stdout_log}" 2>"{stderr_log}"'
    proc = subprocess.run(
        full_command_line,
        cwd=str(cwd),
        timeout=60,
        env=_windows_only_env(),
        # This test's own process may itself be running without a real
        # attached Windows console (e.g. under this agent's own shell
        # harness), with its own stdin/stdout/stderr as redirected pipes.
        # Allocating a fresh (hidden) console for the child -- the same
        # flag the shipped app itself already passes to its own
        # `subprocess.Popen` for this exact script in `apply_update` --
        # gives it real console I/O of its own instead of inheriting
        # whichever console (or lack of one) this test process has.
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    stdout = stdout_log.read_bytes() if stdout_log.exists() else b""
    stderr = stderr_log.read_bytes() if stderr_log.exists() else b""
    return _BatRunResult(proc.returncode, stdout, stderr)


def _cleanup_stray_quickres_processes():
    # Defensive only: hostname.exe/whoami.exe exit near-instantly, well
    # before the script's post-launch `tasklist` check ever runs, so nothing
    # should still be alive by the time the batch script (and this test)
    # finish. Kept as a real safety net in case a slow/loaded machine ever
    # leaves one running past the end of the test.
    subprocess.run(
        ["taskkill", "/F", "/IM", "QuickRes.exe", "/T"],
        capture_output=True,
        env=_windows_only_env(),
    )


class TestRealBatExecutionSuccessPath:
    """The rename-old / reverify / move-new sequence, actually executed by
    cmd.exe end-to-end, produces the real observable filesystem outcome:
    the exe on disk is genuinely swapped for the downloaded one.
    """

    def test_rename_and_move_actually_happen_on_disk(self, monkeypatch, tmp_path):
        # choice.exe (run with no arguments) sits waiting on stdin instead
        # of exiting immediately, so the real, unmocked tasklist check
        # genuinely finds it running and reaches :confirmed -- this is what
        # actually distinguishes a "success" run from a "launch failure"
        # run now that :launchfail auto-restores the backup on an
        # unconfirmed launch (see TestRealBatExecutionLaunchFailurePath
        # below). A fast-exiting stand-in like hostname.exe/whoami.exe
        # would never be confirmed running and would (correctly) trigger
        # that restore instead.
        new_exe_payload = _read_bytes(_CHOICE_EXE)
        old_exe_bytes = b"old-placeholder-contents"

        paths = _generate_real_bat(
            monkeypatch, tmp_path, new_exe_payload, old_exe_bytes
        )

        try:
            result = _run_real_bat(paths["bat_path"], cwd=tmp_path)
            diagnostics = (
                f"returncode={result.returncode} stdout={result.stdout!r} "
                f"stderr={result.stderr!r}"
            )
            # NOT asserted on: the script's own last command is its own
            # self-delete (`del "%~f0"`), deleting the very file cmd.exe is
            # still reading from -- a real, well-known Windows sharing-
            # violation race for that specific idiom. cmd.exe can report a
            # non-zero exit code from that trailing `del` even though the
            # delete (and everything before it) genuinely succeeded, as
            # verified below by the real, observable filesystem state
            # rather than by the process's own reported exit code.

            # The generated script self-deletes ("%~f0") once it reaches
            # :cleanup -- every branch falls through to it, so this holds
            # regardless of launch timing.
            assert not paths["bat_path"].exists(), diagnostics

            # The real log must show the launch was actually CONFIRMED, not
            # rolled back via :launchfail -- proving this run genuinely
            # exercised the success path rather than happening to leave the
            # same bytes behind for an unrelated reason.
            log_text = (
                paths["log_path"].read_text()
                if paths["log_path"].exists()
                else ""
            )
            assert "launch confirmed" in log_text, (diagnostics, log_text)
            assert "launch not confirmed" not in log_text, (diagnostics, log_text)
            # Real evidence the original exe was actually RENAMED to the
            # backup name (not copied/deleted) before the move -- the log
            # line the script only reaches once that `ren` already
            # succeeded.
            assert "renamed old exe" in log_text, (diagnostics, log_text)

            # The staged download must have actually been MOVED into place
            # by the real `move /y`, not merely renamed-around in the
            # script's text.
            assert not paths["new_exe_path"].exists(), diagnostics
            assert paths["exe_path"].exists(), diagnostics
            assert _read_bytes(paths["exe_path"]) == new_exe_payload, diagnostics

            # A genuinely confirmed launch means :confirmed's own cleanup
            # step deleted the now-unneeded backup for real (see
            # TestRealBatExecutionRestorePath and
            # TestRealBatExecutionLaunchFailurePath below for the cases
            # where the backup is instead genuinely restored/kept because
            # the update was rejected or never confirmed running).
            assert not paths["old_backup_path"].exists(), diagnostics
        finally:
            _cleanup_stray_quickres_processes()


class TestRealBatExecutionRestorePath:
    """A staged update that fails its real, actually-executed PowerShell
    reverify (simulating a TOCTOU swap of the staged file after Python's
    own one-time check) must genuinely restore the original exe on disk via
    the real `:restore` branch, not just claim to in its script text.
    """

    def test_failed_reverify_actually_restores_backup_on_disk(
        self, monkeypatch, tmp_path
    ):
        old_exe_bytes = _read_bytes(_HOSTNAME_EXE)
        # A structurally valid PE at generation time, so apply_update's own
        # Python-side integrity check (and thus bat generation) succeeds.
        new_exe_payload = _read_bytes(_WHOAMI_EXE)

        paths = _generate_real_bat(
            monkeypatch, tmp_path, new_exe_payload, old_exe_bytes
        )

        # Simulate a TOCTOU swap: corrupt the already-staged new exe's PE
        # header on disk AFTER generation, before the real script ever
        # runs. This is exactly the attack `_build_reverify_command`'s real,
        # actually-executed PowerShell re-check exists to catch.
        with open(paths["new_exe_path"], "r+b") as f:
            f.seek(0)
            f.write(b"\x00" * 64)

        try:
            result = _run_real_bat(paths["bat_path"], cwd=tmp_path)
            diagnostics = (
                f"returncode={result.returncode} stdout={result.stdout!r} "
                f"stderr={result.stderr!r}"
            )
            # returncode is diagnostic-only here too -- see the matching
            # note in TestRealBatExecutionSuccessPath about the trailing
            # self-delete's own sharing-violation race.

            assert not paths["bat_path"].exists(), diagnostics

            # :restore must have deleted the corrupted staged file for real.
            assert not paths["new_exe_path"].exists(), diagnostics

            # :restore must have renamed the backup back to the canonical
            # exe name for real -- the backup name is gone and the exe path
            # holds the ORIGINAL bytes again, not the (corrupted) new ones.
            assert not paths["old_backup_path"].exists(), diagnostics
            assert paths["exe_path"].exists(), diagnostics
            assert _read_bytes(paths["exe_path"]) == old_exe_bytes, diagnostics
        finally:
            _cleanup_stray_quickres_processes()


class TestRealBatExecutionLaunchFailurePath:
    """A staged update that passes every structural check (PE header,
    optional SHA-256) and is genuinely moved into place, but then never
    shows up as a running process in two real, actually-executed `tasklist`
    checks (simulating a build that is structurally fine but non-functional
    on this machine -- e.g. a missing dependency DLL or wrong architecture),
    must have the real `:launchfail` branch restore the original exe on
    disk, not leave the broken build in place with no automatic recovery.
    """

    def test_launch_never_confirmed_actually_restores_backup_on_disk(
        self, monkeypatch, tmp_path
    ):
        old_exe_bytes = _read_bytes(_HOSTNAME_EXE)
        # hostname.exe, real and UNMODIFIED (unlike the corrupted payload
        # in TestRealBatExecutionRestorePath): it is a genuine, structurally
        # valid PE that passes both the Python-side and the real PowerShell
        # reverify checks, and its own real SHA-256 is not asserted against
        # -- so it also passes the move step for real. It exits in a few
        # milliseconds once launched, though, so the real `tasklist` check
        # below never finds it running by the time either of the script's
        # two real 2-second waits elapses (verified empirically: neither of
        # two consecutive checks ever sees it) -- exactly the "structurally
        # fine, never actually running" case :launchfail exists to recover.
        # (Round 32: the script now retries `start` up to 3 times, each
        # followed by up to `attempt * 3` 2-second polls -- a much larger
        # total grace period than before -- but the per-check cadence
        # itself is unchanged, so a genuinely near-instant exiter like this
        # one is still never observed running by any of those checks.)
        new_exe_payload = _read_bytes(_WHOAMI_EXE)

        paths = _generate_real_bat(
            monkeypatch, tmp_path, new_exe_payload, old_exe_bytes
        )

        try:
            result = _run_real_bat(paths["bat_path"], cwd=tmp_path)
            diagnostics = (
                f"returncode={result.returncode} stdout={result.stdout!r} "
                f"stderr={result.stderr!r}"
            )
            # returncode is diagnostic-only here too -- see the matching
            # note in TestRealBatExecutionSuccessPath about the trailing
            # self-delete's own sharing-violation race.

            assert not paths["bat_path"].exists(), diagnostics

            log_text = (
                paths["log_path"].read_text()
                if paths["log_path"].exists()
                else ""
            )
            assert "launch not confirmed" in log_text, (diagnostics, log_text)

            # The staged download must have actually been MOVED into place
            # first (this is the launch-failure path, reached only after a
            # real, successful move -- unlike the reverify-failure path).
            assert not paths["new_exe_path"].exists(), diagnostics

            # :launchfail must have deleted the broken (but structurally
            # valid) new build sitting at the canonical exe path for real,
            # and renamed the backup back onto it for real -- the exe path
            # holds the ORIGINAL bytes again, not the unconfirmed new ones,
            # and the backup name is gone because it was consumed by that
            # rename.
            assert not paths["old_backup_path"].exists(), diagnostics
            assert paths["exe_path"].exists(), diagnostics
            assert _read_bytes(paths["exe_path"]) == old_exe_bytes, diagnostics
        finally:
            _cleanup_stray_quickres_processes()

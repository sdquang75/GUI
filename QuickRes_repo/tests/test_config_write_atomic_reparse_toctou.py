import glob
import json
import os
import threading

import quickres.config as config


class TestWriteJsonAtomicRereparseCheck:
    """Round 11 fix (Stream B, MEDIUM finding -- TOCTOU): get_app_dir()'s
    reparse-point/junction check (round 5) only ran once, at
    `quickres.config` import time, transitively via the module-level
    `APP_DIR = get_app_dir()` statement. In the elevated helper process
    (main.py's _run_elevated_helper), a narrow window exists between that
    one-time check and the later privileged write_json_atomic() call --
    during which a same-user, unprivileged process (full permissions on its
    own %LOCALAPPDATA%\\QuickRes) could delete and re-plant the directory as
    a junction, and the later elevated write would transparently follow it.

    write_json_atomic() must re-verify its target's containing directory is
    not a reparse point immediately before every write, independent of
    whatever get_app_dir() concluded earlier."""

    def test_refuses_write_when_target_dir_is_reparse_point_at_write_time(
        self, tmp_path, monkeypatch
    ):
        # Simulate the TOCTOU window: get_app_dir()'s earlier check is not
        # consulted at all here -- write_json_atomic() must do its own
        # fresh check on the actual containing directory of `target`.
        target = os.path.join(str(tmp_path), "out.json")
        monkeypatch.setattr(config, "_is_reparse_point", lambda path: True)

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is False
        assert not os.path.exists(target)

    def test_refuses_write_leaves_no_leftover_tmp_file(self, tmp_path, monkeypatch):
        target = os.path.join(str(tmp_path), "out.json")
        monkeypatch.setattr(config, "_is_reparse_point", lambda path: True)

        config.write_json_atomic(target, {"new": "data"})

        leftover_tmp_files = glob.glob(os.path.join(str(tmp_path), "*.tmp*"))
        assert leftover_tmp_files == []

    def test_refuses_write_does_not_corrupt_pre_existing_target(self, tmp_path, monkeypatch):
        target = os.path.join(str(tmp_path), "out.json")
        with open(target, "w", encoding="utf-8") as f:
            json.dump({"pre-existing": True}, f)
        monkeypatch.setattr(config, "_is_reparse_point", lambda path: True)

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is False
        with open(target, "r", encoding="utf-8") as f:
            assert json.load(f) == {"pre-existing": True}

    def test_checks_both_the_target_directory_and_the_tmp_file_path(self, tmp_path, monkeypatch):
        # The containing directory and the per-invocation temp file path
        # (path + f".tmp{pid}.{tid}", fully predictable from this process's
        # own pid/thread id) are both attack surfaces a pre-planted reparse
        # point could sit at. The directory is covered by a plain
        # _is_reparse_point() recheck immediately before open and again
        # immediately before the final os.replace(); the tmp file path is
        # covered by opening it through _open_no_reparse_follow(), which
        # inspects the same handle it opens rather than a separate,
        # racy check-then-open pair -- see that function's own docstring.
        target = os.path.join(str(tmp_path), "out.json")
        expected_tmp_path = target + f".tmp{os.getpid()}.{threading.get_ident()}"
        checked_dirs = []
        opened_paths = []
        monkeypatch.setattr(
            config,
            "_is_reparse_point",
            lambda path: checked_dirs.append(path) or False,
        )
        real_open_no_reparse_follow = config._open_no_reparse_follow

        def spying_open_no_reparse_follow(path):
            opened_paths.append(path)
            return real_open_no_reparse_follow(path)

        monkeypatch.setattr(config, "_open_no_reparse_follow", spying_open_no_reparse_follow)

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is True
        assert checked_dirs == [str(tmp_path), str(tmp_path)]
        assert opened_paths == [expected_tmp_path]

    # A pre-planted reparse point specifically at tmp_path (target directory
    # clean) is covered by TestWriteJsonAtomicAtomicReparseOpen below, via
    # _open_no_reparse_follow() -- the single atomic call that both opens
    # tmp_path and determines whether it is a reparse point, so there is no
    # separate "is it a reparse point" check on tmp_path left to test here.

    def test_write_still_succeeds_when_target_dir_is_not_a_reparse_point(
        self, tmp_path, monkeypatch
    ):
        target = os.path.join(str(tmp_path), "out.json")
        monkeypatch.setattr(config, "_is_reparse_point", lambda path: False)

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is True
        with open(target, "r", encoding="utf-8") as f:
            assert json.load(f) == {"new": "data"}

    def test_stale_get_app_dir_time_check_result_does_not_bypass_write_time_check(
        self, tmp_path, monkeypatch
    ):
        """Proves the fix is a genuine re-check, not a cached/reused result
        from get_app_dir()'s earlier, now-stale pass."""
        target = os.path.join(str(tmp_path), "out.json")

        # get_app_dir()'s one-time check passed (directory was clean then).
        monkeypatch.setattr(config.sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setattr(config, "_is_reparse_point", lambda path: False)
        assert config.get_app_dir() == os.path.join(str(tmp_path), "QuickRes")

        # Attacker window: directory is now (as of write time) a junction.
        monkeypatch.setattr(config, "_is_reparse_point", lambda path: True)

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is False
        assert not os.path.exists(target)


class TestWriteJsonAtomicPostOpenReparseRecheck:
    """Closes the check-then-replace TOCTOU on the target directory:
    _is_reparse_point(target_dir) is checked immediately before open and
    again immediately before the final os.replace(), so a junction planted
    in the window between those two calls is still caught before the rename
    commits. The equivalent risk on tmp_path itself is closed a different
    way -- see TestWriteJsonAtomicAtomicReparseOpen -- since opening it
    through a single atomic call removes the separate check-then-open step
    this class's tmp_path-focused tests used to cover."""

    def test_detects_junction_planted_in_the_window_between_write_and_replace(
        self, tmp_path, monkeypatch
    ):
        # Same race, but at the target_dir/os.replace() boundary: the
        # directory is clean for the pre-open check, but by the time the
        # write has finished and os.replace() is about to run, the
        # containing directory has become a junction.
        target = os.path.join(str(tmp_path), "out.json")
        call_count = {"target_dir": 0}

        def fake_is_reparse_point(path):
            if path == str(tmp_path):
                call_count["target_dir"] += 1
                return call_count["target_dir"] > 1
            return False

        monkeypatch.setattr(config, "_is_reparse_point", fake_is_reparse_point)

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is False
        assert not os.path.exists(target)
        assert call_count["target_dir"] >= 2

    def test_genuine_new_file_write_is_unaffected_by_the_recheck(self, tmp_path, monkeypatch):
        # Regression: no reparse point involved at any point -- the common
        # case of writing a config file that doesn't exist yet must still
        # succeed exactly as before the recheck was added.
        target = os.path.join(str(tmp_path), "out.json")
        monkeypatch.setattr(config, "_is_reparse_point", lambda path: False)

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is True
        with open(target, "r", encoding="utf-8") as f:
            assert json.load(f) == {"new": "data"}

    def test_overwriting_an_existing_file_is_unaffected_by_the_recheck(
        self, tmp_path, monkeypatch
    ):
        # Regression: the other common case -- rewriting an existing config
        # file -- must also still succeed exactly as before.
        target = os.path.join(str(tmp_path), "out.json")
        with open(target, "w", encoding="utf-8") as f:
            json.dump({"old": "data"}, f)
        monkeypatch.setattr(config, "_is_reparse_point", lambda path: False)

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is True
        with open(target, "r", encoding="utf-8") as f:
            assert json.load(f) == {"new": "data"}


class TestWriteJsonAtomicAtomicReparseOpen:
    """A check-then-open sequence, no matter how tight, always leaves a
    window between the check and the open for a symlink to be planted at
    tmp_path. Worse, Windows' CreateFileW -- what Python's own open()
    ultimately calls -- follows a file symlink transparently and, under
    CREATE_ALWAYS semantics, truncates whatever the symlink points at the
    instant the call succeeds, before any later Python-level check can run.
    A recheck performed after open() has already returned is too late to
    prevent that truncation; it can only stop the JSON payload from also
    landing in the truncated target.

    write_json_atomic() now opens tmp_path through a single atomic Win32
    call that passes FILE_FLAG_OPEN_REPARSE_POINT, so a pre-planted reparse
    point is opened as itself -- never as a followed target -- and the
    resulting handle can be inspected for FILE_ATTRIBUTE_REPARSE_POINT with
    no truncation ever having been possible in the first place."""

    def test_refuses_write_when_tmp_path_open_reports_a_reparse_point(
        self, tmp_path, monkeypatch
    ):
        target = os.path.join(str(tmp_path), "out.json")
        monkeypatch.setattr(config, "_open_no_reparse_follow", lambda path: None)

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is False
        assert not os.path.exists(target)

    def test_refuses_write_leaves_no_leftover_tmp_file_when_atomic_open_reports_reparse(
        self, tmp_path, monkeypatch
    ):
        target = os.path.join(str(tmp_path), "out.json")
        monkeypatch.setattr(config, "_open_no_reparse_follow", lambda path: None)

        config.write_json_atomic(target, {"new": "data"})

        leftover_tmp_files = glob.glob(os.path.join(str(tmp_path), "*.tmp*"))
        assert leftover_tmp_files == []

    def test_refuses_write_when_tmp_path_is_a_pre_planted_reparse_point(
        self, tmp_path, monkeypatch
    ):
        target = os.path.join(str(tmp_path), "out.json")
        expected_tmp_path = target + f".tmp{os.getpid()}.{threading.get_ident()}"
        opened_paths = []

        def fake_open_no_reparse_follow(path):
            opened_paths.append(path)
            return None if path == expected_tmp_path else object()

        monkeypatch.setattr(config, "_open_no_reparse_follow", fake_open_no_reparse_follow)

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is False
        assert opened_paths == [expected_tmp_path]

    def test_refuses_write_when_tmp_path_is_reparse_point_without_destroying_followed_targets_content(
        self, tmp_path, monkeypatch
    ):
        # The key proof this fix adds: a pre-planted symlink at tmp_path must
        # be refused WITHOUT its followed target's content ever being
        # destroyed. This is only provable by exercising the real Win32
        # call boundary (CreateFileW/GetFileInformationByHandle), not by
        # mocking a higher-level seam -- a mock of _is_reparse_point or
        # _open_no_reparse_follow can prove the write is refused, but cannot
        # prove that no truncation happened first as a side effect of
        # actually opening the path.
        #
        # This fake CreateFileW models the real distinction the fix relies
        # on: called WITH FILE_FLAG_OPEN_REPARSE_POINT (the fixed
        # behavior), it opens the reparse point object itself and never
        # touches the victim; called WITHOUT it (what plain open() would
        # have done), it simulates Windows transparently following the
        # symlink and truncating the victim as a side effect of the open
        # call succeeding.
        victim = os.path.join(str(tmp_path), "victim.json")
        with open(victim, "w", encoding="utf-8") as f:
            f.write("original-secret-content")

        target = os.path.join(str(tmp_path), "out.json")
        expected_tmp_path = target + f".tmp{os.getpid()}.{threading.get_ident()}"

        real_create_file_w = config.kernel32.CreateFileW
        calls = []

        def fake_create_file_w(filename, access, share, sec_attrs, disposition, flags, template):
            calls.append((filename, flags))
            if filename != expected_tmp_path:
                return real_create_file_w(filename, access, share, sec_attrs, disposition, flags, template)
            if flags & config.FILE_FLAG_OPEN_REPARSE_POINT:
                # Fixed behavior: opens the reparse point object itself.
                return real_create_file_w(
                    expected_tmp_path, access, share, sec_attrs, disposition, flags, template
                )
            # Pre-fix behavior being simulated: Windows follows the symlink
            # and truncates the victim as a side effect of this call alone.
            with open(victim, "w", encoding="utf-8") as vf:
                vf.write("DESTROYED")
            return real_create_file_w(victim, access, share, sec_attrs, disposition, flags, template)

        def fake_get_file_information_by_handle(handle, info_ptr):
            info_ptr.contents.dwFileAttributes = config.FILE_ATTRIBUTE_REPARSE_POINT
            return True

        monkeypatch.setattr(config.kernel32, "CreateFileW", fake_create_file_w)
        monkeypatch.setattr(
            config.kernel32, "GetFileInformationByHandle", fake_get_file_information_by_handle
        )

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is False
        with open(victim, "r", encoding="utf-8") as f:
            assert f.read() == "original-secret-content"
        assert any(
            filename == expected_tmp_path and flags & config.FILE_FLAG_OPEN_REPARSE_POINT
            for filename, flags in calls
        )

    def test_genuine_new_file_write_still_succeeds_through_the_atomic_open(
        self, tmp_path
    ):
        target = os.path.join(str(tmp_path), "out.json")

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is True
        with open(target, "r", encoding="utf-8") as f:
            assert json.load(f) == {"new": "data"}

    def test_overwriting_an_existing_file_still_succeeds_through_the_atomic_open(
        self, tmp_path
    ):
        target = os.path.join(str(tmp_path), "out.json")
        with open(target, "w", encoding="utf-8") as f:
            json.dump({"old": "data", "padding": "x" * 200}, f)

        result = config.write_json_atomic(target, {"new": "data"})

        assert result is True
        with open(target, "r", encoding="utf-8") as f:
            assert json.load(f) == {"new": "data"}

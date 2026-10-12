from quickres import monitors as monitors_mod


def _forbidden_real_elevation(params):
    raise AssertionError(
        "A test reached the REAL monitors._launch_elevated_helper seam, which "
        "would trigger a genuine Windows UAC elevation prompt (ShellExecuteExW "
        "'runas'). Monkeypatch this seam -- or a higher-level function that "
        "wraps it -- before exercising any code path that can reach it."
    )


# Process-wide safety net, applied ONCE at collection time and never undone.
#
# A function-scoped `monkeypatch` fixture is NOT enough here: monkeypatch
# reverts automatically at the end of the single test that requested it. If
# that test arms a real background `threading.Timer` (e.g. the auto-revert
# guard) with a delay longer than the test's own runtime, the test finishes,
# monkeypatch restores the REAL `_launch_elevated_helper`, and the timer
# fires later -- during an unrelated test, or even after the whole suite has
# finished reporting -- calling the real Win32 elevation API and popping a
# live UAC prompt. Patching the module attribute directly here, at import
# time, holds for the entire lifetime of the pytest process regardless of
# individual test teardown, so a straggler timer can never reach the real
# seam. A test that needs to exercise the real seam intentionally can still
# monkeypatch it locally -- monkeypatch's own teardown will restore THIS
# forbidding stand-in, not the real function, since this is the value in
# place before any test ever runs.
monitors_mod._launch_elevated_helper = _forbidden_real_elevation

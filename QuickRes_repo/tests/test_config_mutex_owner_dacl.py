import ctypes

import quickres.config as config


def test_sddl_does_not_deny_everyone_before_allowing_owner():
    """Round 32 fix: the DACL used to be built from the literal SDDL
    "D:(D;;GA;;;WD)(A;;GA;;;OW)" -- an explicit Deny-Everyone (WD) ACE
    listed BEFORE the Allow-Owner (OW) ACE. Windows AccessCheck walks a
    DACL's ACEs in order and stops at the first matching Deny; every
    logged-on user's token (including the object's own owner) contains the
    Everyone/World SID, so that Deny(WD) ACE matched and denied the owner
    too, before the later Allow(OW) ACE was ever consulted. This proves the
    SDDL string built by config no longer contains a Deny ACE that would
    match the owner's own Everyone-SID token membership -- an SD with only
    an explicit Allow-Owner ACE already implicitly denies every other,
    unlisted principal under normal Windows DACL semantics, so dropping the
    Deny-Everyone ACE loses no hardening."""
    assert "D;;GA;;;WD" not in config._MUTEX_SDDL
    assert "(A;;GA;;;OW)" in config._MUTEX_SDDL


def test_local_free_has_pointer_argtypes_declared():
    """Regression guard: kernel32.LocalFree's lone argument is a pointer.
    Without explicit argtypes, ctypes marshals an untyped plain Python int
    as a 32-bit C int/long, which raises OverflowError for any real
    LocalAlloc-backed pointer above the 32-bit range on 64-bit Windows --
    this crashed every real launch via _create_or_open_mutex() despite the
    full test suite passing, because the only test then exercising this
    call site worked around the missing argtypes locally instead of the
    production code declaring them. config.py must declare these at import
    time so every caller of the shared kernel32.LocalFree gets them, not
    just this test."""
    assert config.kernel32.LocalFree.argtypes == [ctypes.c_void_p]
    assert config.kernel32.LocalFree.restype == ctypes.c_void_p


def test_build_owner_only_mutex_security_still_parses_successfully():
    """The edited SDDL string must remain syntactically valid input to the
    real ConvertStringSecurityDescriptorToSecurityDescriptorW Win32 call."""
    sa = config._build_owner_only_mutex_security()
    assert sa is not None
    # kernel32.LocalFree's argtypes/restype are declared once at
    # quickres.config import time -- no per-test workaround needed.
    config.kernel32.LocalFree(sa.lpSecurityDescriptor)


def test_owner_can_reopen_mutex_it_already_created_with_hardened_dacl(monkeypatch):
    """Behavioral regression: creating the mutex once, then opening the
    SAME name again (as the same user -- exactly what happens on an
    ordinary second launch) must succeed with GetLastError()==
    ERROR_ALREADY_EXISTS, never ERROR_ACCESS_DENIED. With the old
    Deny-Everyone-before-Allow-Owner SDDL, the second CreateMutexW call
    failed with ERROR_ACCESS_DENIED because the owner's own token also
    carries the Everyone SID, and _create_or_open_mutex() logged a
    misleading "unexpected error" every time.

    Exercises the real, unmocked kernel32 -- including the real
    kernel32.LocalFree call site inside _create_or_open_mutex() itself,
    proving the module-level argtypes/restype declaration is what actually
    ships, not a test-local stand-in."""
    test_name = f"QuickRes_Test_Mutex_{id(monkeypatch)}"
    monkeypatch.setattr(config, "MUTEX_NAME", test_name)
    log_calls = []
    monkeypatch.setattr(config, "log_msg", lambda msg: log_calls.append(msg))

    mutex1, already_running1 = config._create_or_open_mutex()
    try:
        assert mutex1
        assert already_running1 is False

        mutex2, already_running2 = config._create_or_open_mutex()
        try:
            assert mutex2
            assert already_running2 is True
            # No "unexpected GetLastError()" log entry -- this was the
            # observable symptom of the owner-denies-itself bug.
            assert log_calls == []
        finally:
            if mutex2:
                config.kernel32.CloseHandle(mutex2)
    finally:
        if mutex1:
            config.kernel32.CloseHandle(mutex1)

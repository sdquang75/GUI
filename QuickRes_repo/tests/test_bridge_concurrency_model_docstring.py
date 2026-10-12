"""Round 26 R2 readability finding: the safe way to combine Api's five
concurrency primitives, and the actual meaning of the `_under_lock` naming
suffix, used to be documented only in prose scattered across ~10 separate
method docstrings. A contributor reading only method signatures had no
single place to learn the rule, and the `_under_lock` suffix reads as the
opposite of what it means (it marks the method that ACQUIRES the lock, not
the one that requires the caller to already hold it).

This test guards against that consolidated section disappearing or drifting:
it checks that Api's own docstring names all five primitives, states the
`_under_lock` convention explicitly, and cross-references the pending/guard
method families by name.
"""
from quickres.webview.bridge import Api


def test_docstring_has_concurrency_model_section():
    doc = Api.__doc__ or ""
    assert "concurrency model" in doc.lower(), (
        "Api's docstring should have a single consolidated 'Concurrency "
        "model' section -- see the round 26 R2 finding this guards against"
    )


def test_concurrency_model_names_all_five_primitives():
    doc = Api.__doc__ or ""
    for primitive in (
        "self._op_lock",
        "self._hotkey_lock",
        "self._boot_recovery_lock",
        "self._pending_guard",
        "self._boot_armed",
    ):
        assert primitive in doc, (
            f"Api's consolidated concurrency section should name {primitive}"
        )


def test_concurrency_model_explains_under_lock_convention():
    doc = Api.__doc__ or ""
    lower = doc.lower()
    assert "_under_lock" in doc, (
        "docstring should explicitly call out the _under_lock suffix"
    )
    assert "acquires" in lower and "self-deadlock" in lower, (
        "docstring should state that an _under_lock-suffixed method "
        "ACQUIRES the lock itself, and that wrapping a call to one in an "
        "extra lock acquisition self-deadlocks"
    )
    assert "non-reentrant" in lower, (
        "docstring should explain self-deadlock is possible because "
        "self._op_lock is a plain, non-reentrant lock"
    )


def test_concurrency_model_cross_references_pending_and_guard_families():
    doc = Api.__doc__ or ""
    # Pending-state family: both the caller-must-hold-the-lock method and
    # its acquires-the-lock-itself wrapper must be named.
    assert "_resolve_pending_now_bounded_under_lock" in doc
    assert "_resolve_pending_now" in doc
    # Guard-state family: its acquires-the-lock-itself method must be named.
    assert "_resolve_guard_unbounded_under_lock" in doc

"""R2 Readability finding: the `Api` class docstring documents a naming
convention for every `self._op_lock`-touching helper method -- a name ending
in `_under_lock` ACQUIRES the lock itself, while a name with no such suffix
instead REQUIRES the caller to already hold it, enforced only by a runtime
`assert self._op_lock.locked()` at the top of that method's body. Until this
test, nothing prevented a new helper from violating that convention except a
contributor actually reading the docstring -- getting the suffix wrong (or
omitting it) would only surface later as a genuine deadlock the first time
the mismatched method got called from the wrong kind of context.

This test makes the convention machine-checked instead: it walks every
method defined on `Api`, inspects its source for the literal
precondition-assert pattern (`assert self._op_lock.locked()`), and checks
each match against the naming rule the class docstring actually documents --
a precondition-assert method's name must NOT end in `_under_lock` (that
suffix is reserved for a method that acquires the lock itself, which by
construction must not also assert that it is already held before doing so).
A future contributor who adds a new lock-precondition helper with the wrong
suffix -- either kind of mismatch -- now gets an immediate, specific test
failure instead of a silent trap only a runtime deadlock would reveal.
"""
import inspect
import re

from quickres.webview.bridge import Api

_LOCK_PRECONDITION_ASSERT = re.compile(r"assert\s+self\._op_lock\.locked\(\)")


def _api_methods():
    return [
        (name, member)
        for name, member in inspect.getmembers(Api, predicate=inspect.isfunction)
        if not name.startswith("__")
    ]


def _asserts_lock_precondition(method):
    return _LOCK_PRECONDITION_ASSERT.search(inspect.getsource(method)) is not None


class TestUnderLockNamingConventionIsMachineChecked:
    def test_at_least_one_method_actually_uses_the_precondition_assert(self):
        """Sanity check that this test is exercising real methods, not
        silently passing because the pattern matched nothing at all."""
        matches = [name for name, m in _api_methods() if _asserts_lock_precondition(m)]
        assert matches, (
            "expected at least one Api method to contain the "
            "'assert self._op_lock.locked()' precondition pattern -- if this "
            "fails, the detection regex itself has drifted from the actual "
            "source and needs fixing, not the convention"
        )

    def test_precondition_assert_methods_never_carry_the_under_lock_suffix(self):
        """A method that asserts the lock is already held is, by the class
        docstring's own naming convention, a 'caller must already hold the
        lock' helper -- the opposite of what an `_under_lock` suffix
        promises (that the method acquires the lock itself). Such a method
        must never be named with that suffix."""
        violations = [
            name for name, m in _api_methods()
            if _asserts_lock_precondition(m) and name.endswith("_under_lock")
        ]
        assert violations == [], (
            f"method(s) {violations} contain the lock-already-held "
            "precondition assert but are named as if they acquire the lock "
            "themselves (an '_under_lock' suffix) -- rename to drop the "
            "suffix, since callers must hold self._op_lock BEFORE calling "
            "these, not the other way around"
        )

    def test_under_lock_suffixed_methods_never_carry_the_precondition_assert(self):
        """The mirror-image mistake: a method named with the `_under_lock`
        suffix promises it acquires `self._op_lock` itself, so asserting
        the lock is already held at its own top would be self-contradictory
        -- and, if such a method is ever called from a context that does
        not already hold the lock (the whole point of the suffix), that
        assert would fail immediately instead of the method doing the
        acquire it advertises."""
        violations = [
            name for name, m in _api_methods()
            if name.endswith("_under_lock") and _asserts_lock_precondition(m)
        ]
        assert violations == [], (
            f"method(s) {violations} are named as if they acquire "
            "self._op_lock themselves ('_under_lock' suffix) but also assert "
            "the lock is already held -- either the method no longer "
            "acquires the lock itself (drop the suffix) or the stray "
            "precondition assert must be removed"
        )

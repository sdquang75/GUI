"""Round 15 finding (low, readability): `_resolve_pending_now_under_lock`
and `_resolve_guard_under_lock` share the identical "_X_under_lock" naming
suffix but have OPPOSITE blocking semantics -- the first is a BOUNDED
(short-timeout, give-up-and-return-empty) acquire via
`_LockAcquireGuard.bounded(...)`, the second is an UNBOUNDED (block-forever)
acquire via `_LockAcquireGuard.unbounded(...)`. A contributor adding a third
`self._op_lock`-touching helper following this naming convention has no way
to tell from the name alone which semantics to use.

Fix: renamed to `_resolve_pending_now_bounded_under_lock` and
`_resolve_guard_unbounded_under_lock` so the bounded/unbounded distinction
is encoded directly in the method name, not only in its docstring.
"""
from quickres.webview.bridge import Api


def test_bounded_and_unbounded_names_encode_their_own_semantics():
    assert hasattr(Api, "_resolve_pending_now_bounded_under_lock"), (
        "expected the bounded-acquire helper to be named "
        "_resolve_pending_now_bounded_under_lock"
    )
    assert hasattr(Api, "_resolve_guard_unbounded_under_lock"), (
        "expected the unbounded-acquire helper to be named "
        "_resolve_guard_unbounded_under_lock"
    )


def test_old_ambiguous_names_no_longer_exist():
    assert not hasattr(Api, "_resolve_pending_now_under_lock"), (
        "the old ambiguous name must be fully renamed, not left as an alias"
    )
    assert not hasattr(Api, "_resolve_guard_under_lock"), (
        "the old ambiguous name must be fully renamed, not left as an alias"
    )

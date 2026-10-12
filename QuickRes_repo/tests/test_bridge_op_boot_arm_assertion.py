"""Round 6 readability finding: bridge_op's boot_armed_bypass/releases_boot_arm
kwargs silently do nothing if lock=True isn't also passed -- the wrapper's own
`boot_bypass = lock and boot_armed_bypass and ...` short-circuits to False
whenever lock is falsy. Fixed by raising at decoration time (when bridge_op(...)
is called to build the decorator -- i.e. once per method definition, not once
per call) instead of silently no-op'ing at runtime.
"""
import pytest

from quickres.webview.bridge import bridge_op


class TestBridgeOpRejectsBypassKwargsWithoutLock:
    def test_boot_armed_bypass_without_lock_raises_at_decoration_time(self):
        with pytest.raises(AssertionError):
            bridge_op(boot_armed_bypass=True)

    def test_releases_boot_arm_without_lock_raises_at_decoration_time(self):
        with pytest.raises(AssertionError):
            bridge_op(releases_boot_arm=True)

    def test_both_bypass_kwargs_without_lock_raises_at_decoration_time(self):
        with pytest.raises(AssertionError):
            bridge_op(boot_armed_bypass=True, releases_boot_arm=True)

    def test_raises_before_a_function_is_ever_wrapped(self):
        # The assertion must fire from bridge_op(...) itself -- building the
        # decorator -- not from applying it to a function.
        with pytest.raises(AssertionError):
            bridge_op(releases_boot_arm=True)  # never reaches `deco(fn)`


class TestBridgeOpAllowsValidCombinations:
    def test_lock_alone_is_fine(self):
        deco = bridge_op(lock=True)

        @deco
        def fn(self):
            return "ok"

        assert callable(fn)

    def test_lock_with_both_bypass_kwargs_is_fine(self):
        deco = bridge_op(lock=True, boot_armed_bypass=True, releases_boot_arm=True)

        @deco
        def fn(self):
            return "ok"

        assert callable(fn)

    def test_no_kwargs_at_all_is_fine(self):
        deco = bridge_op()

        @deco
        def fn(self):
            return "ok"

        assert callable(fn)

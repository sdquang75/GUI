"""pywebview window shell.

The pywebview `Window` object is kept at MODULE level (`_WINDOW`), never
as an attribute of the `Api` instance passed in as `js_api` -- a `Window`
reachable from the js_api object triggers a pythonnet marshalling recursion
crash on the EdgeChromium backend. Anything that needs the window
(e.g. a future theme-toggle titlebar action) must go through `get_window()`.
"""

import threading

import webview

from quickres import config
from quickres.config import resource_path
from quickres.webview.bridge import Api

_WINDOW = None

# Upper bound on how long window-close waits for guard resolution
# (`Api._resolve_guard_unbounded_under_lock`, run via `config.call_logged` on a
# background thread below) before giving up on it and continuing shutdown
# regardless. The underlying revert can itself take far longer than this in
# production -- the elevated helper launch it may trigger waits up to 30s,
# and can sit behind an unanswered UAC prompt for the user's whole time on
# that dialog -- so this bound exists purely to keep window-close snappy;
# it is not a correctness requirement. The on-disk pending record is
# written before this call ever runs, and `recover_on_boot()` picks it up
# unconditionally on the next launch, so an unfinished resolution here is
# only a missed convenience, never lost state.
_GUARD_RESOLVE_CLOSE_TIMEOUT_S = 5.0


def get_window():
    """Module-level accessor -- the only sanctioned way to reach the
    pywebview Window object (see the module docstring for why it cannot be
    reached through the `Api`/js_api object instead)."""
    return _WINDOW


def run_app(*, create_window_fn=None, start_fn=None):
    """Create the window and start pywebview's event loop.

    `create_window_fn`/`start_fn` are injectable seams (default to the real
    `webview.create_window`/`webview.start`) so this can be exercised in
    tests without a real GUI toolkit event loop. Returns the constructed
    `Api` instance (tests use this to inspect/monkeypatch it; production
    callers can ignore the return value).
    """
    global _WINDOW
    create_window_fn = create_window_fn or webview.create_window
    start_fn = start_fn or webview.start

    api = Api()
    window = create_window_fn(
        "QuickRes",
        resource_path("quickres/webview/panel.html"),
        js_api=api,
        # Requested size is the OUTER window rect, not the WebView2 client
        # area -- the native title bar + borders eat into it. The original
        # compact size made readable type and the monitor/update dialogs
        # compete for the same vertical space, so leave a comfortable client
        # area for the main controls and status text.
        width=520,
        height=700,
        resizable=False,
    )
    _WINDOW = window

    def _on_closing():
        # Window teardown makes a best-effort attempt to resolve any
        # still-armed 10s auto-revert guard itself before the process
        # exits: the real guard timer is a daemon thread precisely so it
        # never blocks shutdown, but that also means closing the window
        # while a disable is still in its grace period would otherwise kill
        # that timer before it ever fires, leaving the monitor disabled
        # until the user thinks to relaunch. Resolving it here, through the
        # exact same Api._resolve_guard_unbounded_under_lock method the real timer
        # calls, closes that gap when it can complete in time.
        #
        # Like the stop-hotkey call below, this goes through
        # config.call_logged rather than a bare call: window-teardown code
        # is not wrapped by bridge_op's try/except+log_msg (that only
        # covers JS-invoked Api calls), so without this a failure here
        # would crash shutdown or vanish silently under QuickRes.spec's
        # console=False build. It also runs on a background thread that
        # _on_closing joins with a bounded timeout
        # (_GUARD_RESOLVE_CLOSE_TIMEOUT_S) rather than waiting on it
        # directly: the call can perform a real elevated Win32 operation
        # and must never be allowed to hang shutdown indefinitely behind a
        # live UAC prompt. The thread is a daemon, so if it is still
        # running when the timeout elapses, shutdown proceeds without it --
        # the on-disk pending record (already written before this call
        # runs) plus next-launch recover_on_boot is the real safety net,
        # not this best-effort attempt.
        guard = api._pending_guard
        if guard is not None:
            resolver = threading.Thread(
                target=lambda: config.call_logged(
                    api._resolve_guard_unbounded_under_lock, guard,
                    on_error="_on_closing: resolve_guard",
                ),
                daemon=True,
            )
            resolver.start()
            resolver.join(_GUARD_RESOLVE_CLOSE_TIMEOUT_S)

        # Closing the window while stretched must revert to native through
        # the EXACT SAME path as an explicit "stop hotkey" click --
        # delegate to Api._stop_hotkey_impl directly, never a
        # second/duplicated revert implementation here.
        config.call_logged(api._stop_hotkey_impl, on_error="_on_closing: stop_hotkey")

    window.events.closing += _on_closing

    start_fn()
    return api

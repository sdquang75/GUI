import sys

import pytest

import main
from quickres import config


def test_default_import_failure_is_logged_and_shown(monkeypatch):
    # Round 19 (Stream: R4 Resilience): the default-arg lazy import of
    # quickres.webview.app used to sit outside _launch_webview's try/except,
    # so an import-time failure (e.g. a missing PyInstaller hidden import)
    # would propagate out uncaught -- a silent crash under QuickRes.spec's
    # console=False build. Forcing the import itself to fail (by putting
    # None in sys.modules for it, the standard way to make `import`
    # raise ImportError) must be caught by the same log_msg +
    # _show_startup_failure path as a run_app_fn() runtime failure.
    monkeypatch.setitem(sys.modules, "quickres.webview.app", None)

    logged = []
    monkeypatch.setattr(config, "log_msg", lambda msg: logged.append(msg))

    shown = []
    monkeypatch.setattr(main, "_show_startup_failure", lambda message: shown.append(message))

    main._launch_webview()

    assert len(logged) == 1
    assert len(shown) == 1
    assert shown[0].strip() != ""


def test_run_app_exception_is_logged_and_shown_to_user(monkeypatch):
    # Round 10 (Stream 1): _launch_webview() must guard the entire
    # window-creation/event-loop path -- a raised exception (e.g. the
    # Microsoft Edge WebView2 Runtime not being installed) must never
    # crash unhandled under QuickRes.spec's console=False build with zero
    # trace and zero user-visible feedback.
    logged = []
    monkeypatch.setattr(config, "log_msg", lambda msg: logged.append(msg))

    shown = []
    monkeypatch.setattr(main, "_show_startup_failure", lambda message: shown.append(message))

    def _raising_run_app():
        raise RuntimeError("WebView2 Runtime not found")

    main._launch_webview(run_app_fn=_raising_run_app)

    assert len(logged) == 1
    assert "WebView2 Runtime not found" in logged[0]

    assert len(shown) == 1
    assert shown[0].strip() != ""


def test_run_app_success_does_not_log_or_show_dialog(monkeypatch):
    logged = []
    monkeypatch.setattr(config, "log_msg", lambda msg: logged.append(msg))

    shown = []
    monkeypatch.setattr(main, "_show_startup_failure", lambda message: shown.append(message))

    calls = []
    main._launch_webview(run_app_fn=lambda: calls.append(1))

    assert calls == [1]
    assert logged == []
    assert shown == []


def test_show_startup_failure_calls_message_box_w(monkeypatch):
    # Verifies _show_startup_failure itself is the seam that reaches
    # MessageBoxW, so the test above (which monkeypatches
    # _show_startup_failure wholesale) is proven to actually stand in for
    # a real dialog rather than a no-op stub.
    calls = []

    class _FakeUser32:
        def MessageBoxW(self, hwnd, text, caption, flags):
            calls.append((hwnd, text, caption, flags))

    monkeypatch.setattr(main.ctypes, "windll", type("W", (), {"user32": _FakeUser32()})())

    main._show_startup_failure("some message")

    assert len(calls) == 1
    _, text, caption, _ = calls[0]
    assert text == "some message"
    assert caption == "QuickRes"

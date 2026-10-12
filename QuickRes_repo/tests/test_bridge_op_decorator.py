import threading

import pytest

from quickres.webview.bridge import bridge_op


class _FakeApi:
    """Minimal stand-in for Api -- only what bridge_op needs (_op_lock,
    _lock_reason). Deliberately NOT the real Api class (T3.1 tests the
    decorator in isolation, before Api itself is built in T3.2/T3.3).
    """

    def __init__(self):
        self._op_lock = threading.Lock()
        self._lock_reason = None


def test_successful_call_returns_ok_envelope_with_data():
    class Fake(_FakeApi):
        @bridge_op()
        def double(self, n):
            return n * 2

    result = Fake().double(21)

    assert result == {"ok": True, "kind": "ok", "data": 42, "message": ""}


def test_raising_call_returns_error_envelope_and_logs(monkeypatch):
    logged = []
    monkeypatch.setattr("quickres.webview.bridge.log_msg", lambda msg: logged.append(msg))

    class Fake(_FakeApi):
        @bridge_op()
        def boom(self):
            raise ValueError("nope")

    result = Fake().boom()

    assert result == {"ok": False, "kind": "error", "data": None, "message": "nope"}
    assert len(logged) == 1
    assert "boom failed" in logged[0]


def test_lock_true_busy_when_already_held():
    class Fake(_FakeApi):
        @bridge_op(lock=True)
        def guarded(self):
            return "should not run"

    fake = Fake()
    fake._op_lock.acquire()
    fake._lock_reason = "already busy doing X"

    result = fake.guarded()

    assert result == {
        "ok": False, "kind": "busy", "data": None, "message": "already busy doing X",
    }


def test_lock_true_default_busy_message_when_no_reason_set():
    class Fake(_FakeApi):
        @bridge_op(lock=True)
        def guarded(self):
            return "should not run"

    fake = Fake()
    fake._op_lock.acquire()

    result = fake.guarded()

    assert result["message"] == "Another monitor operation is in progress"


def test_lock_true_success_releases_lock_after():
    class Fake(_FakeApi):
        @bridge_op(lock=True)
        def guarded(self):
            return "ran"

    fake = Fake()
    result = fake.guarded()

    assert result == {"ok": True, "kind": "ok", "data": "ran", "message": ""}
    # Lock must be free again -- a fresh non-blocking acquire succeeds.
    assert fake._op_lock.acquire(blocking=False) is True


def test_lock_true_exception_still_releases_lock():
    class Fake(_FakeApi):
        @bridge_op(lock=True)
        def guarded(self):
            raise RuntimeError("boom")

    fake = Fake()
    fake.guarded()

    assert fake._op_lock.acquire(blocking=False) is True


def test_lock_false_never_touches_the_lock():
    class Fake(_FakeApi):
        @bridge_op()
        def unguarded(self):
            return "ran"

    fake = Fake()
    fake._op_lock.acquire()  # hold it externally

    result = fake.unguarded()

    assert result == {"ok": True, "kind": "ok", "data": "ran", "message": ""}

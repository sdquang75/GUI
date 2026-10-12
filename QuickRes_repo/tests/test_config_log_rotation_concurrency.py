import os
import threading
import time

import quickres.config as config


def test_log_msg_rotate_and_write_sequence_is_mutually_exclusive(tmp_path, monkeypatch):
    """log_msg()/_rotate_log_if_needed() had no lock, unlike every other
    piece of shared mutable state in this file (config._update_lock,
    i18n._lang_lock, write_json_atomic's thread-id-suffixed tmp names) that
    was hardened for the same "pywebview dispatches every JS->Python bridge
    call on its own thread" concurrency model. Without a lock, one thread's
    os.replace(LOG_PATH, LOG_PATH + '.old') could fire while another thread
    still held an open append handle to the old inode, silently losing that
    write -- contradicting LOG_MAX_BYTES's own docstring guarantee that
    nothing is silently deleted mid-session.

    This proves the rotate-check-then-append sequence is now atomic with
    respect to other log_msg() callers: one thread is parked mid-sequence
    (via an instrumented _rotate_log_if_needed that blocks until released),
    a second thread's log_msg() call is started while the first is still
    parked there, and the test asserts the second call never observes the
    first as "already inside" that sequence concurrently -- i.e. at most
    one thread is ever inside the guarded rotate+write sequence at once.
    Pre-fix (no lock), the second thread would enter its own
    _rotate_log_if_needed() call immediately, alongside the first, bumping
    the observed concurrency above 1.
    """
    log_path = os.path.join(str(tmp_path), "quickres.log")
    monkeypatch.setattr(config, "LOG_PATH", log_path)
    # Large enough that no real rotation ever actually triggers -- this test
    # isolates the *locking* behavior around the rotate-check+write
    # sequence, independent of the single-backup clobber behavior that is
    # rotation's own (intentional) design.
    monkeypatch.setattr(config, "LOG_MAX_BYTES", 1_000_000)

    counter_lock = threading.Lock()
    state = {"in_section": 0, "max_concurrent": 0}
    first_entered = threading.Event()
    release_first = threading.Event()
    calls_seen = {"count": 0}

    orig_rotate = config._rotate_log_if_needed

    def instrumented_rotate():
        with counter_lock:
            state["in_section"] += 1
            state["max_concurrent"] = max(state["max_concurrent"], state["in_section"])
        calls_seen["count"] += 1
        if calls_seen["count"] == 1:
            first_entered.set()
            release_first.wait(timeout=5)
        orig_rotate()
        with counter_lock:
            state["in_section"] -= 1

    monkeypatch.setattr(config, "_rotate_log_if_needed", instrumented_rotate)

    def writer(payload):
        config.log_msg(payload)

    t1 = threading.Thread(target=writer, args=("first",))
    t1.start()
    assert first_entered.wait(timeout=5), "first thread never reached the guarded section"

    t2 = threading.Thread(target=writer, args=("second",))
    t2.start()
    # Give the second thread every opportunity to race into the guarded
    # section while the first is still parked inside it, if nothing is
    # serializing them.
    time.sleep(0.3)

    release_first.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive()
    assert not t2.is_alive()

    assert state["max_concurrent"] == 1, (
        "two threads were inside log_msg()'s rotate+write sequence at the "
        "same time -- the sequence is not atomic against concurrent "
        "log_msg() callers"
    )

    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "first" in content
    assert "second" in content

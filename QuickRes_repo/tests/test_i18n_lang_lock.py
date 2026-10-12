"""Round 6 corrective fix, Stream 5 (quickres/i18n.py only): module-level
_current_lang was mutated with no lock, even though pywebview dispatches
every JS->Python bridge call on its own thread -- the same concurrency model
that config._update_lock/Api._op_lock/Api._hotkey_lock are all already
documented as guarding against. These tests prove set_language() (the write)
and get_language()/t() (the reads) all serialize through the same lock, using
real threading.Thread workers -- matching the pattern established in
tests/test_config_update_lock.py.
"""

import threading
import time

from quickres import i18n


def test_module_exposes_a_lock_guarding_current_lang():
    # Regression guard: the fix must add an actual threading.Lock, not just
    # reorder code. A bare attribute here would fail this basic shape check.
    assert isinstance(i18n._lang_lock, type(threading.Lock()))


def test_set_language_serializes_through_the_lock(monkeypatch):
    # Regression test for the unsynchronized write to _current_lang.
    # set_language() must acquire the module lock for its whole
    # check-and-assign, so a caller already holding the lock blocks any
    # other thread's set_language() call until it releases.
    monkeypatch.setattr(i18n, "_current_lang", "en")
    acquired = i18n._lang_lock.acquire(timeout=1)
    assert acquired, "test setup failed to acquire the lock"
    try:
        worker = threading.Thread(target=i18n.set_language, args=("ru",))
        worker.start()
        time.sleep(0.15)
        # Lock is still held by the main thread -> the worker must be
        # blocked before it ever writes _current_lang.
        assert worker.is_alive()
        assert i18n._current_lang == "en"
    finally:
        i18n._lang_lock.release()

    worker.join(timeout=2)
    assert not worker.is_alive()
    assert i18n._current_lang == "ru"


def test_get_language_serializes_through_the_lock(monkeypatch):
    # Regression test for the unsynchronized read of _current_lang.
    # get_language() must also acquire the module lock, so a caller
    # already holding the lock blocks any other thread's read until release.
    monkeypatch.setattr(i18n, "_current_lang", "ru")
    acquired = i18n._lang_lock.acquire(timeout=1)
    assert acquired, "test setup failed to acquire the lock"

    result = {}

    def _read():
        result["lang"] = i18n.get_language()

    try:
        worker = threading.Thread(target=_read)
        worker.start()
        time.sleep(0.15)
        assert worker.is_alive()
        assert "lang" not in result
    finally:
        i18n._lang_lock.release()

    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result.get("lang") == "ru"


def test_t_serializes_through_the_lock(monkeypatch):
    # Same guarantee as get_language(), but for t()'s internal read of
    # _current_lang used to pick which STRINGS bundle to format from.
    monkeypatch.setattr(i18n, "_current_lang", "ru")
    acquired = i18n._lang_lock.acquire(timeout=1)
    assert acquired, "test setup failed to acquire the lock"

    result = {}

    def _read():
        result["text"] = i18n.t("app_title")

    try:
        worker = threading.Thread(target=_read)
        worker.start()
        time.sleep(0.15)
        assert worker.is_alive()
        assert "text" not in result
    finally:
        i18n._lang_lock.release()

    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result.get("text") == i18n.STRINGS["ru"]["app_title"]


def test_concurrent_set_language_and_reads_never_corrupt_state(monkeypatch):
    # Stress test with real thread workers hammering set_language() against
    # concurrent get_language()/t() reads. Without the lock this is racy at
    # the interpreter level; with it, every observed value must be one of
    # the two valid languages actually being set, and nothing may raise.
    monkeypatch.setattr(i18n, "_current_lang", "en")
    errors = []
    observed = []
    iterations = 200

    def _writer(lang):
        for _ in range(iterations):
            try:
                i18n.set_language(lang)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

    def _reader():
        for _ in range(iterations):
            try:
                lang = i18n.get_language()
                text = i18n.t("app_title")
                observed.append((lang, text))
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

    threads = [
        threading.Thread(target=_writer, args=("en",)),
        threading.Thread(target=_writer, args=("ru",)),
        threading.Thread(target=_reader),
        threading.Thread(target=_reader),
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=10)
        assert not th.is_alive()

    assert not errors
    for lang, text in observed:
        assert lang in ("en", "ru")
        assert text == i18n.STRINGS[lang]["app_title"]

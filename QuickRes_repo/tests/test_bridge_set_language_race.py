"""Round 12 (Reliability finding): i18n.t() re-reads the shared global
_current_lang independently on every call instead of snapshotting it once
per bundle. bridge.py's set_language (@bridge_op(), no lock=True) is not
serialized against other bridge-dispatch calls. Two rapid set_language
calls on different threads can interleave: while thread A is still
building its strings/faq bundle key-by-key, thread B's set_language can
mutate the shared i18n global in between two of A's per-key lookups,
producing a response whose language.resolved disagrees with its own
strings/faq content.

The fix pins the resolved language into a local snapshot once per call and
threads it explicitly through every i18n.t() call in the bundle (via
_ui_strings(lang)/_faq_bundle(lang) and i18n.t(key, lang=...)), so mutating
the shared global mid-bundle can no longer affect an in-flight response.
"""
import threading

import pytest

from quickres.webview import bridge as bridge_mod
from quickres.webview.bridge import Api
from quickres import config
from quickres import i18n


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


@pytest.fixture(autouse=True)
def _reset_lang():
    yield
    i18n.set_language("en")


class TestSetLanguageResponseInternallyConsistentUnderInterleaving:
    def test_two_interleaved_set_language_calls_never_mix_languages_in_one_response(
        self, monkeypatch
    ):
        api_ru = Api()
        api_en = Api()

        entered_first_lookup = threading.Event()
        release_first_lookup = threading.Event()
        real_t = i18n.t
        call_count = {"n": 0}

        def paced_t(key, lang=None, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Simulate thread A's bundle build being paused right after
                # its very first per-key lookup, giving thread B a window
                # to land its own set_language call (which mutates the
                # shared i18n global) before thread A finishes building.
                entered_first_lookup.set()
                release_first_lookup.wait(timeout=2)
            return real_t(key, lang=lang, **kwargs)

        monkeypatch.setattr(bridge_mod.i18n, "t", paced_t)

        results = {}

        def _call_ru():
            results["ru"] = api_ru.set_language("ru")

        thread_a = threading.Thread(target=_call_ru)
        thread_a.start()
        assert entered_first_lookup.wait(timeout=2), "thread A never reached its first i18n.t() call"

        # Thread B: an independent, concurrent set_language call that
        # mutates the shared i18n._current_lang global while thread A's
        # bundle is still mid-build.
        results["en"] = api_en.set_language("en")

        release_first_lookup.set()
        thread_a.join(timeout=2)

        data_ru = results["ru"]["data"]
        assert data_ru["language"]["resolved"] == "ru"
        for key, value in data_ru["strings"].items():
            expected = i18n.STRINGS["ru"].get(key) or i18n.STRINGS["en"].get(key, key)
            assert value == expected, (
                f"strings[{key!r}] == {value!r} disagrees with resolved language "
                f"'ru' under a concurrent set_language('en') interleaving"
            )
        for entry, (q_key, a_key) in zip(data_ru["faq"], bridge_mod._FAQ_KEYS):
            assert entry["q"] == (i18n.STRINGS["ru"].get(q_key) or i18n.STRINGS["en"][q_key])
            assert entry["a"] == (i18n.STRINGS["ru"].get(a_key) or i18n.STRINGS["en"][a_key])

        data_en = results["en"]["data"]
        assert data_en["language"]["resolved"] == "en"
        for key, value in data_en["strings"].items():
            assert value == i18n.STRINGS["en"][key]


class TestPinnedLangHelpers:
    """Direct unit coverage that the bundle helpers accept and honor an
    explicit `lang` snapshot instead of always re-reading the global."""

    def test_ui_strings_honors_pinned_lang_even_if_global_differs(self):
        i18n.set_language("en")
        strings = bridge_mod._ui_strings("ru")
        assert strings["btn_apply"] == i18n.STRINGS["ru"]["btn_apply"]

    def test_faq_bundle_honors_pinned_lang_even_if_global_differs(self):
        i18n.set_language("en")
        faq = bridge_mod._faq_bundle("ru")
        assert faq[0]["q"] == i18n.STRINGS["ru"]["faq_q1"]

    def test_i18n_t_lang_param_overrides_global(self):
        i18n.set_language("en")
        assert i18n.t("btn_apply", lang="ru") == i18n.STRINGS["ru"]["btn_apply"]
        assert i18n.t("btn_apply") == i18n.STRINGS["en"]["btn_apply"]

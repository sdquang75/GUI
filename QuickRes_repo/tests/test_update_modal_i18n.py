"""RED->GREEN coverage for corrective batch `webview-security-reliability-
fixes`, round 6, Stream 2 (panel.html): wiring the "Updates" footer button to
the real auto-update UI flow (download/verify/apply pipeline already existed
in updater.py/bridge.py -- confirm_update -- but nothing in the UI ever
triggered it before this round). This file covers the Python-side half of
that fix -- the new i18n keys the update-available modal uses must exist in
both STRINGS languages and be bundled by bridge.py's `_ui_strings()`,
matching the established pattern (see test_boot_error_i18n.py /
test_i18n_preset_kind_labels.py).
"""

from quickres import i18n
from quickres.webview import bridge

UPDATE_MODAL_KEYS = [
    "update_available_title",
    "update_available_body",
    "btn_update_now",
    "btn_later",
    "btn_retry_download",
    "update_downloading",
    "update_downloading_unknown",
    "update_verifying",
    "update_ready",
    "update_installing",
    "update_failed",
]


def test_update_modal_keys_present_in_both_languages():
    for lang in ("en", "ru"):
        for key in UPDATE_MODAL_KEYS:
            assert key in i18n.STRINGS[lang], f"missing {key!r} in STRINGS[{lang!r}]"
            assert i18n.STRINGS[lang][key], f"{key!r} in STRINGS[{lang!r}] must not be empty"


def test_update_available_body_has_version_placeholder():
    for lang in ("en", "ru"):
        assert "{version}" in i18n.STRINGS[lang]["update_available_body"], (
            f"update_available_body in STRINGS[{lang!r}] must keep the {{version}} "
            f"placeholder for the new version number"
        )


def test_update_modal_keys_bundled_by_ui_strings():
    original_lang = i18n.get_language()
    i18n.set_language("en")
    bundle = bridge._ui_strings()
    for key in UPDATE_MODAL_KEYS:
        assert key in bundle, f"_ui_strings() must bundle {key!r}"
        assert bundle[key] == i18n.STRINGS["en"][key]
    i18n.set_language(original_lang)

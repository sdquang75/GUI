"""RED->GREEN coverage for corrective batch `webview-security-reliability-
fixes`, round 5, Stream D finding: panel.html's boot() had no persistent
retry/fallback UI when `get_initial_state` fails. This file covers the
Python-side half of that fix -- the new i18n keys the persistent error
banner uses (`boot_error_title`, `boot_error_body`, `btn_retry`) must exist
in both STRINGS languages and be bundled by bridge.py's `_ui_strings()`,
matching the established pattern (see test_i18n_preset_kind_labels.py).
"""

from quickres import i18n
from quickres.webview import bridge

BOOT_ERROR_KEYS = ["boot_error_title", "boot_error_body", "btn_retry"]


def test_boot_error_keys_present_in_both_languages():
    for lang in ("en", "ru"):
        for key in BOOT_ERROR_KEYS:
            assert key in i18n.STRINGS[lang], f"missing {key!r} in STRINGS[{lang!r}]"
            assert i18n.STRINGS[lang][key], f"{key!r} in STRINGS[{lang!r}] must not be empty"


def test_boot_error_keys_bundled_by_ui_strings():
    original_lang = i18n.get_language()
    i18n.set_language("en")
    bundle = bridge._ui_strings()
    for key in BOOT_ERROR_KEYS:
        assert key in bundle, f"_ui_strings() must bundle {key!r}"
        assert bundle[key] == i18n.STRINGS["en"][key]
    i18n.set_language(original_lang)

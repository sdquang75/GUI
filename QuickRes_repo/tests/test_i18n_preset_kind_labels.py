"""RED->GREEN coverage for corrective batch `webview-security-reliability-
fixes`, Stream 3 finding 3d: the preset-card kind labels ("Native"/
"Stretched"/"Low", panel.html's renderResSection) must route through the
i18n STRINGS/_ui_strings() bundle like every other visible chrome label,
instead of being hardcoded English in the JS.
"""

from quickres import i18n
from quickres.webview import bridge

PRESET_KIND_KEYS = ["preset_kind_native", "preset_kind_stretched", "preset_kind_low"]


def test_preset_kind_keys_present_in_both_languages():
    for lang in ("en", "ru"):
        for key in PRESET_KIND_KEYS:
            assert key in i18n.STRINGS[lang], f"missing {key!r} in STRINGS[{lang!r}]"
            assert i18n.STRINGS[lang][key], f"{key!r} in STRINGS[{lang!r}] must not be empty"


def test_preset_kind_keys_bundled_by_ui_strings():
    original_lang = i18n.get_language()
    i18n.set_language("en")
    bundle = bridge._ui_strings()
    for key in PRESET_KIND_KEYS:
        assert key in bundle, f"_ui_strings() must bundle {key!r}"
        assert bundle[key] == i18n.STRINGS["en"][key]
    i18n.set_language(original_lang)

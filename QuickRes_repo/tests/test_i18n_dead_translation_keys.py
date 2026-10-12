"""Corrective batch `webview-security-reliability-fixes`, round 11 finding
(Stream E, quickres/i18n.py only): STRINGS["en"]/STRINGS["ru"] carried dead
keys left over from the deleted Tkinter GUI, never read by any current code
path. The only readers of `i18n.t()` are bridge.py's `_ui_strings()`
allowlist and `_faq_bundle()`'s `_FAQ_KEYS`; a key absent from both is dead
and must not exist in either language dict.

This test locks that contract: a representative sample of keys confirmed
dead (mechanically, via a repo-wide grep for each key name turning up no
reference outside i18n.py itself) must stay absent from both STRINGS["en"]
and STRINGS["ru"], so a future edit cannot silently reintroduce Tkinter-era
dead weight.
"""

from quickres import i18n

_CONFIRMED_DEAD_KEYS = [
    "hotkey_label",
    "btn_settings",
    "restore_banner_text",
    "status_custom_cleared",
    "status_hotkey_stopped",
    "status_res_format_error",
    "status_custom_format_error",
    "status_removed_custom",
    "dialog_res_not_found_body",
    "btn_close",
    "settings_window_title",
    "settings_theme_label",
    "btn_reset_custom_res",
    "btn_check_update",
    "monitors_none_found",
    "status_write_flag_failed",
    "status_requesting_disable",
    "status_requesting_enable",
    "status_disable_pending",
    "status_disable_unconfirmed",
    "status_enable_pending",
    "status_enable_unconfirmed",
    "revert_countdown",
    "status_reverting",
    "status_revert_pending",
    "status_device_gone",
    "status_reverted",
    "status_reverted_unconfirmed",
    "status_revert_failed",
    "status_restoring",
    "status_restore_pending",
    "status_restored",
    "status_restored_unconfirmed",
    "status_restore_failed",
    "status_unexpected_error",
    "custom_count_of_max",
]


def test_dead_tkinter_gui_keys_absent_from_both_languages():
    for key in _CONFIRMED_DEAD_KEYS:
        assert key not in i18n.STRINGS["en"], f"dead key {key!r} still in STRINGS['en']"
        assert key not in i18n.STRINGS["ru"], f"dead key {key!r} still in STRINGS['ru']"

"""Round 17 finding 2(a) (R4 Resilience, HIGH): `_remove_targets_from_pending`
was the ONE target-iteration site in bridge.py that omitted the
`isinstance(t, dict)` guard every sibling site uses
(`_check_no_stale_record_conflict`, `_clear_force_unlocked_targets_from_pending`,
`_pending_target_ids_from_disk`). If `pending_restore.json`'s `targets` list
ever contains a non-dict entry -- a partially-written record from a crash
mid-write, an older schema, external tampering/AV interference -- the bare
`t.get("instance_id")` in its list comprehension raised `AttributeError`
right there. Reached from the real auto-revert `threading.Timer` callback (via
`_resolve_guard_unbounded_under_lock` -> `_clear_or_trim_pending_record`),
that exception had nothing above it to catch it, so it vanished into
Python's default `threading.excepthook` -- unreachable in the console=False
packaged build -- leaving the guard permanently unresolved with no further
revert attempt ever scheduled and no trace in quickres.log.
"""
from quickres.webview.bridge import Api
from quickres import config

import pytest


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


class TestRemoveTargetsFromPendingToleratesNonDictEntries:
    def test_non_dict_entry_does_not_raise(self):
        config.save_pending({
            "action": "disable",
            "targets": [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A"},
                "a-corrupted-non-dict-entry",
                None,
            ],
            "result_file": None,
            "helper_pid": None,
            "owner_pid": 1234,
            "started_at": 0,
            "unlocked_at": None,
        })
        api = Api()

        # Must not raise AttributeError -- the whole point of this fix.
        api._remove_targets_from_pending({"DISPLAY\\A\\1"})

    def test_non_dict_entry_is_dropped_dict_entries_preserved(self):
        config.save_pending({
            "action": "disable",
            "targets": [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A"},
                {"instance_id": "DISPLAY\\B\\2", "friendly_name": "B"},
                "a-corrupted-non-dict-entry",
            ],
            "result_file": None,
            "helper_pid": None,
            "owner_pid": 1234,
            "started_at": 0,
            "unlocked_at": None,
        })
        api = Api()

        api._remove_targets_from_pending({"DISPLAY\\A\\1"})

        record = config.load_pending()
        assert record is not None
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\B\\2"}

    def test_all_dict_entries_removed_with_only_a_non_dict_survivor_clears_file(self):
        config.save_pending({
            "action": "disable",
            "targets": [
                {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A"},
                "a-corrupted-non-dict-entry",
            ],
            "result_file": None,
            "helper_pid": None,
            "owner_pid": 1234,
            "started_at": 0,
            "unlocked_at": None,
        })
        api = Api()

        # Removing the only real dict target leaves nothing usable behind --
        # the corrupted entry is dropped (it carries no instance_id to key
        # on), so the record is cleared like the ordinary empty-remainder case.
        api._remove_targets_from_pending({"DISPLAY\\A\\1"})

        assert config.load_pending() is None

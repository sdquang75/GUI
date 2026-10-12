import pytest

from quickres.recovery import normalize_pending


def _valid_record(n_targets=1):
    return {
        "action": "disable",
        "targets": [
            {"instance_id": f"DISPLAY\\ACR0{i}\\4&abc&0&UID{i}", "friendly_name": f"Monitor {i}"}
            for i in range(n_targets)
        ],
        "result_file": "C:\\AppDir\\monitor_op_result_1_2.json",
        "helper_pid": 111,
        "owner_pid": 222,
        "started_at": 1000.0,
        "unlocked_at": None,
    }


def test_valid_single_target_record_normalizes():
    raw = _valid_record(1)
    result = normalize_pending(raw, mtime=999.0)
    assert result is not None
    assert len(result["targets"]) == 1
    assert result["action"] == "disable"


def test_valid_three_target_record_normalizes():
    raw = _valid_record(3)
    result = normalize_pending(raw, mtime=999.0)
    assert result is not None
    assert len(result["targets"]) == 3


def test_raw_none_returns_none():
    assert normalize_pending(None, mtime=999.0) is None


@pytest.mark.parametrize("bad_raw", [["not", "a", "dict"], "a string", 42])
def test_raw_not_a_dict_returns_none(bad_raw):
    assert normalize_pending(bad_raw, mtime=999.0) is None


def test_action_not_disable_returns_none():
    raw = _valid_record(1)
    raw["action"] = "enable"
    assert normalize_pending(raw, mtime=999.0) is None


def test_targets_missing_returns_none():
    raw = _valid_record(1)
    del raw["targets"]
    assert normalize_pending(raw, mtime=999.0) is None


def test_targets_empty_list_returns_none():
    raw = _valid_record(1)
    raw["targets"] = []
    assert normalize_pending(raw, mtime=999.0) is None


def test_targets_contains_non_dict_element_returns_none():
    raw = _valid_record(1)
    raw["targets"].append("not-a-dict")
    assert normalize_pending(raw, mtime=999.0) is None


@pytest.mark.parametrize("bad_instance_id", [None, ""])
def test_target_missing_or_empty_instance_id_returns_none(bad_instance_id):
    raw = _valid_record(1)
    raw["targets"][0]["instance_id"] = bad_instance_id
    assert normalize_pending(raw, mtime=999.0) is None


def test_target_without_instance_id_key_returns_none():
    raw = _valid_record(1)
    del raw["targets"][0]["instance_id"]
    assert normalize_pending(raw, mtime=999.0) is None


def test_legacy_schema_1_instance_id_returns_none():
    raw = {"action": "disable", "instance_id": "DISPLAY\\ACR0123\\4&abc&0&UID256"}
    assert normalize_pending(raw, mtime=999.0) is None


def test_legacy_schema_2_target_instance_id_returns_none():
    raw = {"action": "disable", "target_instance_id": "DISPLAY\\ACR0123\\4&abc&0&UID256"}
    assert normalize_pending(raw, mtime=999.0) is None


def test_legacy_schema_3_instances_returns_none():
    raw = {"action": "disable", "instances": [{"instance_id": "DISPLAY\\ACR0123\\4&abc&0&UID256"}]}
    assert normalize_pending(raw, mtime=999.0) is None


def test_started_at_absent_falls_back_to_mtime():
    raw = _valid_record(1)
    del raw["started_at"]
    result = normalize_pending(raw, mtime=555.0)
    assert result is not None
    assert result["started_at"] == 555.0


def test_unlocked_at_absent_defaults_to_none():
    raw = _valid_record(1)
    del raw["unlocked_at"]
    result = normalize_pending(raw, mtime=999.0)
    assert result is not None
    assert result["unlocked_at"] is None


def test_friendly_name_absent_defaults_to_empty_string():
    raw = _valid_record(1)
    del raw["targets"][0]["friendly_name"]
    result = normalize_pending(raw, mtime=999.0)
    assert result is not None
    assert result["targets"][0]["friendly_name"] == ""


@pytest.mark.parametrize(
    "bad_started_at", ["not-a-number", [1, 2], {"a": 1}, True, False, "1000.0"]
)
def test_started_at_non_numeric_falls_back_to_mtime(bad_started_at):
    # A malformed on-disk pending_restore.json (disk corruption that still
    # parses as valid JSON, a partial write from an older build, or manual
    # editing) could carry a truthy but non-numeric started_at. Only
    # checking truthiness (the old `raw.get("started_at") or mtime`) would
    # let it pass straight through and later blow up resolve_pending's
    # `now - started_at` with a TypeError -- it must instead degrade the
    # same way an absent started_at already does, falling back to mtime.
    raw = _valid_record(1)
    raw["started_at"] = bad_started_at
    result = normalize_pending(raw, mtime=777.0)
    assert result is not None
    assert result["started_at"] == 777.0


def test_started_at_valid_int_is_preserved():
    raw = _valid_record(1)
    raw["started_at"] = 12345
    result = normalize_pending(raw, mtime=999.0)
    assert result is not None
    assert result["started_at"] == 12345

import os

import pytest

from quickres.recovery import is_safe_result_path


def test_valid_path_inside_app_dir_is_safe(tmp_path):
    app_dir = str(tmp_path)
    path = os.path.join(app_dir, "monitor_op_result_1234_5678.json")
    assert is_safe_result_path(path, app_dir) is True


@pytest.mark.parametrize("quote_char", ['"', "'"])
def test_path_with_quote_char_is_unsafe(tmp_path, quote_char):
    app_dir = str(tmp_path)
    path = os.path.join(app_dir, f"monitor_op_result_1234_5678{quote_char}.json")
    assert is_safe_result_path(path, app_dir) is False


@pytest.mark.parametrize(
    "basename",
    ["foo.json", "monitor_op_result_abc_123.json", "monitor_op_result_1234.json"],
)
def test_wrong_basename_shape_is_unsafe(tmp_path, basename):
    app_dir = str(tmp_path)
    path = os.path.join(app_dir, basename)
    assert is_safe_result_path(path, app_dir) is False


def test_path_in_different_directory_is_unsafe(tmp_path):
    app_dir = str(tmp_path / "app")
    other_dir = str(tmp_path / "other")
    os.makedirs(app_dir, exist_ok=True)
    os.makedirs(other_dir, exist_ok=True)
    path = os.path.join(other_dir, "monitor_op_result_1234_5678.json")
    assert is_safe_result_path(path, app_dir) is False


def test_traversal_resolving_outside_app_dir_is_unsafe(tmp_path):
    app_dir = str(tmp_path / "app")
    os.makedirs(app_dir, exist_ok=True)
    path = os.path.join(app_dir, "..", "monitor_op_result_1234_5678.json")
    assert is_safe_result_path(path, app_dir) is False


def test_traversal_resolving_back_inside_app_dir_is_safe(tmp_path):
    app_dir = str(tmp_path / "app")
    sub_dir = os.path.join(app_dir, "sub")
    os.makedirs(sub_dir, exist_ok=True)
    # sub/../monitor_op_result_....json abspath-normalizes back to app_dir itself.
    path = os.path.join(sub_dir, "..", "monitor_op_result_1234_5678.json")
    assert is_safe_result_path(path, app_dir) is True

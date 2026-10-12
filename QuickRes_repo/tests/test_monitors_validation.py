import pytest

from quickres.monitors import is_valid_instance_id


@pytest.mark.parametrize(
    "instance_id, expected",
    [
        (r"DISPLAY\ACR0123\4&abc&0&UID256", True),
        ("DISPLAY ACR0123", False),  # space
        ('DISPLAY"ACR0123', False),  # quote
        ("DISPLAY;ACR0123", False),  # shell metachar ;
        ("DISPLAY|ACR0123", False),  # shell metachar |
        ("DISPLAY$(whoami)", False),  # shell metachar $(
        ("", False),  # empty string
        # 1d: a trailing (odd or even count) backslash breaks Windows argv
        # quoting when interpolated into --instance-id "{id}" followed by
        # --result-file "{path}" -- a real device instance id never
        # legitimately ends in a backslash, so reject trailing backslash
        # entirely regardless of run length.
        ("DISPLAY\\ACR0123\\", False),  # single trailing backslash (odd)
        ("DISPLAY\\ACR0123\\\\", False),  # double trailing backslash (even)
        ("DISPLAY\\ACR0123\\\\\\", False),  # triple trailing backslash (odd)
    ],
)
def test_is_valid_instance_id(instance_id, expected):
    assert is_valid_instance_id(instance_id) is expected

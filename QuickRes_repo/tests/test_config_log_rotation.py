import os

import quickres.config as config


def test_log_below_threshold_appends_normally_with_no_rotation(tmp_path, monkeypatch):
    log_path = os.path.join(str(tmp_path), "quickres.log")
    monkeypatch.setattr(config, "LOG_PATH", log_path)
    monkeypatch.setattr(config, "LOG_MAX_BYTES", 1024)

    config.log_msg("first line")
    config.log_msg("second line")
    config.log_msg("third line")

    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "first line" in content
    assert "second line" in content
    assert "third line" in content
    assert not os.path.exists(log_path + ".old")


def test_log_past_threshold_rotates_instead_of_growing_unbounded(tmp_path, monkeypatch):
    log_path = os.path.join(str(tmp_path), "quickres.log")
    monkeypatch.setattr(config, "LOG_PATH", log_path)
    monkeypatch.setattr(config, "LOG_MAX_BYTES", 100)

    # Pre-seed a log file already past the threshold, simulating a
    # long-running install that has accumulated a lot of log output.
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("x" * 200 + "\n")

    config.log_msg("new line after rotation")

    # The old oversized content moved to a single .old backup, not deleted
    # outright and not left in place to keep growing.
    old_path = log_path + ".old"
    assert os.path.exists(old_path)
    with open(old_path, "r", encoding="utf-8") as f:
        assert "x" * 200 in f.read()

    # The active log starts fresh -- it must not still contain the
    # oversized pre-existing content, only the new entry.
    with open(log_path, "r", encoding="utf-8") as f:
        fresh_content = f.read()
    assert "x" * 200 not in fresh_content
    assert "new line after rotation" in fresh_content


def test_log_rotation_overwrites_previous_old_backup(tmp_path, monkeypatch):
    log_path = os.path.join(str(tmp_path), "quickres.log")
    old_path = log_path + ".old"
    monkeypatch.setattr(config, "LOG_PATH", log_path)
    monkeypatch.setattr(config, "LOG_MAX_BYTES", 100)

    with open(old_path, "w", encoding="utf-8") as f:
        f.write("stale backup from a previous rotation\n")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("y" * 200 + "\n")

    config.log_msg("triggers second rotation")

    with open(old_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "y" * 200 in content
    assert "stale backup" not in content

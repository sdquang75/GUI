import json

from quickres import monitors


def _session(tmp_path, ids=("A",)):
    return monitors.GuardedDisableSession(
        instance_ids=ids,
        command_path=str(tmp_path / "monitor_guard_command_1_1.json"),
        completion_path=str(tmp_path / "monitor_guard_result_1_1.json"),
        app_dir=str(tmp_path),
    )


def test_revert_guarded_disable_consumes_the_helper_result_and_confirms_state(
    monkeypatch, tmp_path
):
    session = _session(tmp_path)
    with open(session.completion_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "action": "revert",
                "results": [{"instance_id": "A", "ok": True, "message": "enabled"}],
            },
            f,
        )
    monkeypatch.setattr(monitors, "sample_device_states", lambda _ids: {"A": True})

    assert monitors.revert_guarded_disable(session) == [
        ("A", True, "enabled", monitors.OUTCOME_CONFIRMED)
    ]
    assert not tmp_path.joinpath("monitor_guard_result_1_1.json").exists()


def test_keep_guarded_disable_only_accepts_a_matching_keep_acknowledgement(tmp_path):
    session = _session(tmp_path)
    with open(session.completion_path, "w", encoding="utf-8") as f:
        json.dump({"action": "auto_revert", "results": []}, f)

    assert monitors.keep_guarded_disable(session) is False


def test_signal_guarded_disable_writes_only_the_terminal_action(monkeypatch, tmp_path):
    session = _session(tmp_path)

    def complete_after_command(got):
        with open(got.command_path, "r", encoding="utf-8") as f:
            assert json.load(f) == {"action": "revert"}
        return {"action": "revert", "results": []}

    monkeypatch.setattr(monitors, "_wait_for_guard_completion", complete_after_command)

    assert monitors._signal_guarded_disable(session, "revert") == {
        "action": "revert", "results": []
    }
    assert session.command_requested is True

from quickres import monitors
from quickres.webview import bridge


def _session(tmp_path, ids=("A",)):
    return monitors.GuardedDisableSession(
        instance_ids=ids,
        command_path=str(tmp_path / "monitor_guard_command_1_1.json"),
        completion_path=str(tmp_path / "monitor_guard_result_1_1.json"),
        app_dir=str(tmp_path),
    )


def _ok(ids):
    return [
        (instance_id, True, "enabled", monitors.OUTCOME_CONFIRMED)
        for instance_id in ids
    ]


def test_revert_now_uses_the_active_guarded_helper_without_a_second_elevation(
    monkeypatch, tmp_path
):
    api = bridge.Api()
    session = _session(tmp_path)
    calls = []

    def disable_with_guard(ids, enabled, **_kwargs):
        assert enabled is False
        context = monitors._guarded_disable_local.context
        context.session = session
        calls.append((tuple(ids), enabled))
        return [("A", True, "disabled", monitors.OUTCOME_CONFIRMED)]

    monkeypatch.setattr(monitors, "set_monitors_enabled", disable_with_guard)
    monkeypatch.setattr(api, "_build_and_save_pending_record", lambda *_: {"targets": []})
    monkeypatch.setattr(api, "_arm_guard_timer", lambda *_: None)
    monkeypatch.setattr(api, "_clear_or_trim_pending_record", lambda *_args, **_kwargs: None)
    reverted = []
    monkeypatch.setattr(
        monitors,
        "revert_guarded_disable",
        lambda got: reverted.append(got) or _ok(got.instance_ids),
    )

    api.set_monitors_enabled(["A"], False)
    result = api.revert_now()

    assert calls == [(('A',), False)]
    assert reverted == [session]
    assert result["data"]["results"] == _ok(("A",))


def test_keep_disabled_closes_the_active_guarded_helper(monkeypatch, tmp_path):
    api = bridge.Api()
    session = _session(tmp_path)
    guard = bridge.PendingDisableGuard(
        armed_at=0, target_ids=["A"], revert_fn=lambda _ids: _ok(("A",))
    )
    api._pending_guard = guard
    api._guarded_disable_session = session
    kept = []
    monkeypatch.setattr(monitors, "keep_guarded_disable", lambda got: kept.append(got) or True)
    monkeypatch.setattr(api, "_clear_or_trim_pending_record", lambda *_args, **_kwargs: None)

    assert api.keep_disabled()["data"] == {"kept": True}
    assert kept == [session]
    assert api._pending_guard is None
    assert api._guarded_disable_session is None


def test_auto_revert_uses_guarded_helper_before_the_normal_elevation(monkeypatch, tmp_path):
    api = bridge.Api()
    session = _session(tmp_path)
    reverted = []
    api._guarded_disable_session = session
    guard = bridge.PendingDisableGuard(
        armed_at=0,
        target_ids=["A"],
        revert_fn=lambda ids: api._revert_guarded_session_or_elevate(guard, ids),
    )
    api._pending_guard = guard
    monkeypatch.setattr(
        monitors,
        "revert_guarded_disable",
        lambda got: reverted.append(got) or _ok(got.instance_ids),
    )
    monkeypatch.setattr(api, "_clear_or_trim_pending_record", lambda *_args, **_kwargs: None)

    api._resolve_guard_unbounded_under_lock(guard, now=10)

    assert reverted == [session]
    assert guard.resolved is True
    assert api._guarded_disable_session is None

import json
import os
import sys

import pytest

import main
from quickres import config


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    # 1d: _run_elevated_helper now validates args.result_file via
    # recovery.is_safe_result_path(path, config.APP_DIR) before writing --
    # every test's result_file must live under (a monkeypatched) APP_DIR and
    # match the monitor_op_result_<pid>_<ms>.json shape.
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    yield


def _fake_worker_op(results_by_id):
    def worker(op, instance_id):
        ok, message = results_by_id[instance_id]
        if isinstance(ok, Exception):
            raise ok
        return ok, message
    return worker


def test_single_instance_id_produces_one_result_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(
        main, "run_elevated_worker_op", _fake_worker_op({"A": (True, "Disabled")})
    )
    result_file = str(tmp_path / "monitor_op_result_111_222.json")

    exit_code = main._run_elevated_helper(
        ["--monitor-op", "disable", "--instance-id", "A", "--result-file", result_file]
    )

    assert exit_code == 0
    with open(result_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data == {"results": [{"instance_id": "A", "ok": True, "message": "Disabled"}]}


def test_three_instance_id_occurrences_produce_three_result_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(
        main,
        "run_elevated_worker_op",
        _fake_worker_op(
            {
                "A": (True, "Disabled"),
                "B": (True, "Disabled"),
                "C": (True, "Disabled"),
            }
        ),
    )
    result_file = str(tmp_path / "monitor_op_result_111_223.json")

    exit_code = main._run_elevated_helper(
        [
            "--monitor-op", "disable",
            "--instance-id", "A",
            "--instance-id", "B",
            "--instance-id", "C",
            "--result-file", result_file,
        ]
    )

    assert exit_code == 0
    with open(result_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert [r["instance_id"] for r in data["results"]] == ["A", "B", "C"]
    assert all(r["ok"] is True for r in data["results"])


def test_one_device_raises_internally_others_still_processed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        main,
        "run_elevated_worker_op",
        _fake_worker_op(
            {
                "A": (True, "Disabled"),
                "B": (RuntimeError("boom"), None),
                "C": (True, "Disabled"),
            }
        ),
    )
    result_file = str(tmp_path / "monitor_op_result_111_224.json")

    exit_code = main._run_elevated_helper(
        [
            "--monitor-op", "disable",
            "--instance-id", "A",
            "--instance-id", "B",
            "--instance-id", "C",
            "--result-file", result_file,
        ]
    )

    assert exit_code == 1  # not all ok
    with open(result_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    results = {r["instance_id"]: r for r in data["results"]}
    assert results["A"]["ok"] is True
    assert results["C"]["ok"] is True
    assert results["B"]["ok"] is False
    assert "boom" in results["B"]["message"]


def test_unsafe_result_file_path_is_refused_before_writing(monkeypatch, tmp_path):
    # 1d: main.py trusts args.result_file from its own argv unconditionally
    # today -- mirror monitors.read_op_result's read-side check with a
    # write-side recovery.is_safe_result_path validation. A path outside
    # APP_DIR (or with the wrong basename shape) must never be written to.
    monkeypatch.setattr(
        main, "run_elevated_worker_op", _fake_worker_op({"A": (True, "Disabled")})
    )
    outside_dir = tmp_path.parent / "outside_main_helper"
    outside_dir.mkdir(exist_ok=True)
    unsafe_result_file = str(outside_dir / "monitor_op_result_111_225.json")

    exit_code = main._run_elevated_helper(
        ["--monitor-op", "disable", "--instance-id", "A", "--result-file", unsafe_result_file]
    )

    assert exit_code == 1
    assert not os.path.exists(unsafe_result_file)


def test_unsafe_result_file_path_diagnostic_survives_console_false_build(monkeypatch, tmp_path):
    # QuickRes.spec builds with console=False, which leaves both sys.stdout
    # and sys.stderr as None (no console attached, same as pythonw.exe) --
    # print(..., file=sys.stderr) with sys.stderr=None falls back to
    # sys.stdout (CPython's print() treats an explicit file=None the same
    # as an omitted one), so both must be None to reproduce the real
    # frozen-build crash: print() then tries None.write() and raises
    # AttributeError instead of the intended clean return 1. The diagnostic
    # must still reach an observable place (quickres.log via
    # config.log_msg) and the function must still return exit code 1.
    monkeypatch.setattr(
        main, "run_elevated_worker_op", _fake_worker_op({"A": (True, "Disabled")})
    )
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    logged = []
    monkeypatch.setattr(config, "log_msg", lambda msg: logged.append(msg))

    outside_dir = tmp_path.parent / "outside_main_helper_console_false"
    outside_dir.mkdir(exist_ok=True)
    unsafe_result_file = str(outside_dir / "monitor_op_result_111_226.json")

    exit_code = main._run_elevated_helper(
        ["--monitor-op", "disable", "--instance-id", "A", "--result-file", unsafe_result_file]
    )

    assert exit_code == 1
    assert not os.path.exists(unsafe_result_file)
    assert len(logged) == 1
    assert unsafe_result_file in logged[0]


def test_no_batch_flag_code_path_exists():
    with open("main.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "--batch" not in content


def test_guarded_disable_reuses_the_same_elevated_helper_for_revert(monkeypatch, tmp_path):
    calls = []

    def worker(op, instance_id):
        calls.append((op, instance_id))
        return True, f"{op} {instance_id}"

    monkeypatch.setattr(main, "run_elevated_worker_op", worker)
    result_file = str(tmp_path / "monitor_op_result_111_227.json")
    command_file = tmp_path / "monitor_guard_command_111_227.json"
    completion_file = tmp_path / "monitor_guard_result_111_227.json"
    command_file.write_text(json.dumps({"action": "revert"}), encoding="utf-8")

    exit_code = main._run_elevated_helper(
        [
            "--monitor-op", "guarded-disable",
            "--instance-id", "A",
            "--result-file", result_file,
            "--guard-command-file", str(command_file),
            "--guard-result-file", str(completion_file),
            "--guard-timeout-s", "1",
        ]
    )

    assert exit_code == 0
    assert calls == [("disable", "A"), ("enable", "A")]
    assert json.loads(completion_file.read_text(encoding="utf-8")) == {
        "action": "revert",
        "results": [{"instance_id": "A", "ok": True, "message": "enable A"}],
    }


def test_guarded_disable_refuses_an_unsafe_command_path_before_any_operation(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(main, "run_elevated_worker_op", lambda *args: calls.append(args))
    result_file = str(tmp_path / "monitor_op_result_111_228.json")
    completion_file = tmp_path / "monitor_guard_result_111_228.json"

    exit_code = main._run_elevated_helper(
        [
            "--monitor-op", "guarded-disable",
            "--instance-id", "A",
            "--result-file", result_file,
            "--guard-command-file", str(tmp_path.parent / "monitor_guard_command_111_228.json"),
            "--guard-result-file", str(completion_file),
            "--guard-timeout-s", "1",
        ]
    )

    assert exit_code == 1
    assert calls == []


def test_guarded_disable_auto_reverts_when_no_command_arrives(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        main,
        "run_elevated_worker_op",
        lambda op, instance_id: calls.append((op, instance_id)) or (True, op),
    )
    monotonic_values = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(main.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)
    result_file = str(tmp_path / "monitor_op_result_111_229.json")
    command_file = tmp_path / "monitor_guard_command_111_229.json"
    completion_file = tmp_path / "monitor_guard_result_111_229.json"

    exit_code = main._run_elevated_helper(
        [
            "--monitor-op", "guarded-disable",
            "--instance-id", "A",
            "--result-file", result_file,
            "--guard-command-file", str(command_file),
            "--guard-result-file", str(completion_file),
            "--guard-timeout-s", "1",
        ]
    )

    assert exit_code == 0
    assert calls == [("disable", "A"), ("enable", "A")]
    assert json.loads(completion_file.read_text(encoding="utf-8"))["action"] == "auto_revert"

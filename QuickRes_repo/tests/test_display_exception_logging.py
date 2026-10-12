"""Round 10 (Stream 3) -- detect_gpu_vendors(), launch_appx_app(), and
launch_start_app() each swallow their best-effort subprocess failure with a
bare `except Exception` and no diagnostic trace, unlike every other
background/best-effort failure path in this codebase (config.py, monitors.py,
bridge.py, hotkey.py all route through `quickres.config.log_msg`). These
tests assert each function still returns its safe fallback value on failure
*and* records the failure via `log_msg` so it leaves a trace instead of
vanishing silently.
"""

from quickres import display


def _raise(*_args, **_kwargs):
    raise OSError("boom")


def test_detect_gpu_vendors_logs_on_subprocess_failure(monkeypatch):
    logged = []
    monkeypatch.setattr(display, "log_msg", lambda msg: logged.append(msg))
    monkeypatch.setattr(display.subprocess, "run", _raise)

    result = display.detect_gpu_vendors()

    assert result == set()
    assert len(logged) == 1
    assert "boom" in logged[0]


def test_launch_appx_app_logs_on_subprocess_failure(monkeypatch):
    logged = []
    monkeypatch.setattr(display, "log_msg", lambda msg: logged.append(msg))
    monkeypatch.setattr(display.subprocess, "run", _raise)

    result = display.launch_appx_app("Graphics")

    assert result is False
    assert len(logged) == 1
    assert "boom" in logged[0]


def test_launch_start_app_logs_on_subprocess_failure(monkeypatch):
    logged = []
    monkeypatch.setattr(display, "log_msg", lambda msg: logged.append(msg))
    monkeypatch.setattr(display.subprocess, "run", _raise)

    result = display.launch_start_app("NVIDIA Control Panel")

    assert result is False
    assert len(logged) == 1
    assert "boom" in logged[0]

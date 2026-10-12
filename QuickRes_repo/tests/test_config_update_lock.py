import os
import threading
import time

import quickres.config as config


def _use_tmp_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", os.path.join(str(tmp_path), "config.json"))


def _make_slow_load(orig_load, delay):
    def _slow_load():
        cfg = orig_load()
        # Widen the window between the read and the write so two concurrent
        # update_config() callers are very likely to both read the same
        # pre-update snapshot when the read-modify-write isn't serialized.
        time.sleep(delay)
        return cfg

    return _slow_load


def test_concurrent_update_config_calls_do_not_lose_either_update(monkeypatch, tmp_path):
    # Regression test for the unsynchronized read-modify-write race in
    # update_config(): pywebview dispatches every JS->Python bridge call on
    # its own new thread, and set_theme/set_language/start_hotkey all call
    # update_config() with no lock. Two callers racing on separate threads
    # can each load the same stale config, apply their own single key, and
    # save -- whichever save lands last silently discards the other
    # caller's key. update_config() must serialize its whole
    # load-modify-save sequence so both updates survive.
    _use_tmp_paths(monkeypatch, tmp_path)
    config.save_config({})

    orig_load = config.load_config
    monkeypatch.setattr(config, "load_config", _make_slow_load(orig_load, 0.2))

    def _update_lang():
        config.update_config({"language": "en"})

    def _update_theme():
        config.update_config({"theme": "dark"})

    t1 = threading.Thread(target=_update_lang)
    t2 = threading.Thread(target=_update_theme)

    t1.start()
    time.sleep(0.05)  # Ensure t1 has already entered load_config's read.
    t2.start()

    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive()
    assert not t2.is_alive()

    final = orig_load()
    assert final.get("language") == "en"
    assert final.get("theme") == "dark"

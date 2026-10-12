import glob
import json
import os
import threading

import quickres.config as config


def test_concurrent_writes_from_different_threads_use_unique_tmp_paths(tmp_path, monkeypatch):
    """pywebview dispatches every JS->Python bridge call on its own new
    thread. Several bridge_op methods write config with no lock over the
    write, so two rapid UI actions can call write_json_atomic() for the same
    target path concurrently on separate threads. Pre-fix, tmp_path was
    derived only from os.getpid() -- identical across threads in the same
    process -- so both threads raced to open/write/replace the SAME temp
    file, letting one write silently clobber the other's temp file before it
    was renamed into place. The fix must give each invocation a genuinely
    unique temp filename (e.g. by also folding in threading.get_ident()),
    not just a per-process one.
    """
    target = os.path.join(str(tmp_path), "out.json")
    captured_tmp_paths = []
    barrier = threading.Barrier(2)
    real_replace = os.replace

    def spy_replace(src, dst):
        captured_tmp_paths.append(src)
        # Force both threads to have already computed/used their tmp_path
        # before either one is allowed to actually rename it into place --
        # this proves the two writes were genuinely in flight at once, not
        # just sequential.
        barrier.wait(timeout=2)
        return real_replace(src, dst)

    monkeypatch.setattr(config.os, "replace", spy_replace)

    def worker(n):
        config.write_json_atomic(target, {"n": n})

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(captured_tmp_paths) == 2
    assert captured_tmp_paths[0] != captured_tmp_paths[1], (
        "both threads computed the same tmp_path -- concurrent writes can "
        "collide and silently discard each other's update"
    )

    # No corruption: the final file is valid JSON matching one of the two
    # writes (last-writer-wins is acceptable; garbled interleaved bytes are
    # not).
    with open(target, "r", encoding="utf-8") as f:
        final = json.load(f)
    assert final in ({"n": 1}, {"n": 2})

    leftover_tmp_files = glob.glob(os.path.join(str(tmp_path), "*.tmp*"))
    assert leftover_tmp_files == []

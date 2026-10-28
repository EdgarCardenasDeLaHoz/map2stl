"""geo2stl.cache atomic-write and lock primitives."""
import json
import os

import pytest

from geo2stl.cache import atomic_write, atomic_write_json, file_lock, try_lock_fd, unlock_fd


def test_atomic_write_json_roundtrip_creates_folder(tmp_path):
    p = tmp_path / "a" / "b" / "x.json"
    atomic_write_json(p, {"k": [1, 2], "é": "ü"}, indent=1, sort_keys=True)
    assert json.loads(p.read_text(encoding="utf-8")) == {"k": [1, 2], "é": "ü"}
    assert [f.name for f in p.parent.iterdir()] == ["x.json"]


def test_failing_write_keeps_old_file_and_leaves_no_temp(tmp_path):
    p = tmp_path / "x.bin"
    atomic_write(p, lambda f: f.write(b"old"))

    def boom(f):
        f.write(b"partial")
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        atomic_write(p, boom)
    assert p.read_bytes() == b"old"
    assert [f.name for f in tmp_path.iterdir()] == ["x.bin"]


def test_file_lock_excludes_second_holder_and_frees_on_exit(tmp_path):
    target = tmp_path / "shared.json"
    with file_lock(target):
        with pytest.raises(TimeoutError):
            with file_lock(target, timeout_s=0.3):
                pass
    with file_lock(target, timeout_s=0.3):
        pass


def test_try_lock_fd_basic(tmp_path):
    path = tmp_path / "l.lock"
    a = os.open(path, os.O_RDWR | os.O_CREAT)
    b = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        assert try_lock_fd(a)
        assert not try_lock_fd(b)
        unlock_fd(a)
        assert try_lock_fd(b)
        unlock_fd(b)
    finally:
        os.close(a)
        os.close(b)

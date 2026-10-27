"""Tests for src.run_lock: file-based advisory lock."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from src.run_lock import (
    RunLocked,
    acquire_lock,
    engine_lock,
    read_lock,
    release_lock,
)


@pytest.fixture
def lock_dir(tmp_path):
    return tmp_path / "logs"


@pytest.fixture
def lock_path(lock_dir):
    return lock_dir / "engine.lock"


class TestAcquireRelease:
    def test_acquire_creates_lock_file(self, lock_path):
        result = acquire_lock("test", lock_path=lock_path)
        assert result == lock_path
        assert lock_path.exists()
        info = json.loads(lock_path.read_text(encoding="utf-8"))
        assert info["caller"] == "test"
        assert info["pid"] == os.getpid()
        assert isinstance(info["started"], float)

    def test_release_removes_lock_file(self, lock_path):
        acquire_lock("test", lock_path=lock_path)
        assert lock_path.exists()
        release_lock(lock_path)
        assert not lock_path.exists()

    def test_release_idempotent(self, lock_path):
        release_lock(lock_path)
        release_lock(lock_path)

    def test_creates_parent_dirs(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "engine.lock"
        acquire_lock("test", lock_path=deep)
        assert deep.exists()
        release_lock(deep)


class TestConcurrentRejection:
    def test_second_acquire_raises(self, lock_path):
        acquire_lock("first", lock_path=lock_path)
        with pytest.raises(RunLocked, match="Another run is in progress"):
            acquire_lock("second", lock_path=lock_path)
        release_lock(lock_path)

    def test_after_release_can_acquire_again(self, lock_path):
        acquire_lock("first", lock_path=lock_path)
        release_lock(lock_path)
        acquire_lock("second", lock_path=lock_path)
        info = json.loads(lock_path.read_text(encoding="utf-8"))
        assert info["caller"] == "second"
        release_lock(lock_path)


class TestStaleLock:
    def test_stale_lock_is_cleared(self, lock_path):
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps({
            "pid": 99999,
            "started": time.time() - 3600,
            "caller": "old_process",
        }), encoding="utf-8")
        acquire_lock("new", lock_path=lock_path, stale_seconds=1800)
        info = json.loads(lock_path.read_text(encoding="utf-8"))
        assert info["caller"] == "new"
        release_lock(lock_path)

    def test_fresh_lock_not_cleared(self, lock_path):
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps({
            "pid": 99999,
            "started": time.time(),
            "caller": "active_process",
        }), encoding="utf-8")
        with pytest.raises(RunLocked):
            acquire_lock("new", lock_path=lock_path, stale_seconds=1800)

    def test_stale_with_bad_json(self, lock_path):
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("not json", encoding="utf-8")
        os.utime(lock_path, (time.time() - 3600, time.time() - 3600))
        acquire_lock("new", lock_path=lock_path, stale_seconds=1800)
        assert lock_path.exists()
        release_lock(lock_path)


class TestContextManager:
    def test_context_manager_acquires_and_releases(self, lock_path):
        with engine_lock("ctx_test", lock_path=lock_path):
            assert lock_path.exists()
            info = json.loads(lock_path.read_text(encoding="utf-8"))
            assert info["caller"] == "ctx_test"
        assert not lock_path.exists()

    def test_context_manager_releases_on_exception(self, lock_path):
        with pytest.raises(ValueError):
            with engine_lock("ctx_test", lock_path=lock_path):
                raise ValueError("boom")
        assert not lock_path.exists()

    def test_nested_lock_raises(self, lock_path):
        with engine_lock("first", lock_path=lock_path):
            with pytest.raises(RunLocked):
                with engine_lock("second", lock_path=lock_path):
                    pass
        assert not lock_path.exists()


class TestReadLock:
    def test_read_no_lock(self, lock_path):
        assert read_lock(lock_path) is None

    def test_read_existing_lock(self, lock_path):
        acquire_lock("reader_test", lock_path=lock_path)
        info = read_lock(lock_path)
        assert info["caller"] == "reader_test"
        release_lock(lock_path)

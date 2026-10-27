"""File-based advisory lock for the attendance engine.

Prevents two runs (CLI, agents, web app) from overlapping. The lock is a
file in 07_LOGS/ whose content is a JSON dict with the PID, start time
and caller identity. A stale-lock timeout (default 30 min) auto-clears
locks left by crashed processes.

Usage::

    from src.run_lock import engine_lock, RunLocked

    with engine_lock("monthly_report"):
        daily.run_month(month)

Or non-context-manager::

    acquire_lock("cli")
    try:
        ...
    finally:
        release_lock()
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

LOCK_STALE_SECONDS = 30 * 60  # 30 minutes

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGS_DIR = _PROJECT_ROOT / "07_LOGS"
_LOCK_PATH = _LOGS_DIR / "engine.lock"


class RunLocked(Exception):
    """Another run is already in progress."""


def _lock_info(caller: str) -> str:
    return json.dumps({
        "pid": os.getpid(),
        "started": time.time(),
        "caller": caller,
    })


def _is_stale(lock_path: Path, stale_seconds: float = LOCK_STALE_SECONDS) -> bool:
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        age = time.time() - data.get("started", 0)
        return age >= stale_seconds
    except (OSError, ValueError, KeyError):
        try:
            age = time.time() - lock_path.stat().st_mtime
            return age >= stale_seconds
        except OSError:
            return True


def acquire_lock(
    caller: str = "unknown",
    lock_path: Path | None = None,
    stale_seconds: float = LOCK_STALE_SECONDS,
) -> Path:
    """Create the lock file atomically. Returns the lock path on success.

    Raises RunLocked if another non-stale lock exists.
    """
    lp = lock_path or _LOCK_PATH
    lp.parent.mkdir(parents=True, exist_ok=True)

    if lp.exists():
        if _is_stale(lp, stale_seconds):
            try:
                lp.unlink()
            except OSError:
                pass
        else:
            try:
                info = json.loads(lp.read_text(encoding="utf-8"))
                holder = f"pid={info.get('pid')}, caller={info.get('caller')}"
            except (OSError, ValueError):
                holder = "unknown"
            raise RunLocked(
                f"Another run is in progress ({holder}). "
                f"Lock file: {lp}. "
                f"If the previous run crashed, wait {stale_seconds // 60:.0f} min "
                f"or delete the lock file manually."
            )

    # Atomic create: O_CREAT | O_EXCL fails if the file appeared between
    # our check and this open. On Windows, this is advisory but sufficient
    # for the single-PC use case.
    try:
        fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RunLocked(
            f"Another run acquired the lock just now. Lock file: {lp}."
        )
    try:
        os.write(fd, _lock_info(caller).encode("utf-8"))
    finally:
        os.close(fd)

    return lp


def release_lock(lock_path: Path | None = None) -> None:
    """Remove the lock file. Safe to call even if it doesn't exist."""
    lp = lock_path or _LOCK_PATH
    try:
        lp.unlink()
    except FileNotFoundError:
        pass


def read_lock(lock_path: Path | None = None) -> dict | None:
    """Read the current lock info, or None if no lock exists."""
    lp = lock_path or _LOCK_PATH
    if not lp.exists():
        return None
    try:
        return json.loads(lp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"exists": True}


class engine_lock:
    """Context manager for the engine lock."""

    def __init__(
        self,
        caller: str = "unknown",
        lock_path: Path | None = None,
        stale_seconds: float = LOCK_STALE_SECONDS,
    ):
        self._caller = caller
        self._lock_path = lock_path
        self._stale_seconds = stale_seconds
        self._acquired_path: Path | None = None

    def __enter__(self):
        self._acquired_path = acquire_lock(
            self._caller, self._lock_path, self._stale_seconds,
        )
        return self

    def __exit__(self, *exc_info):
        if self._acquired_path:
            release_lock(self._acquired_path)
            self._acquired_path = None

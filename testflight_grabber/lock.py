from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import IO, Optional


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, lock_path: Path, pid_path: Path) -> None:
        self.lock_path = lock_path
        self.pid_path = pid_path
        self._handle: Optional[IO[str]] = None

    def __enter__(self) -> "InstanceLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise AlreadyRunning("another monitor instance holds the lock") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        self.pid_path.write_text(f"{os.getpid()}\n", encoding="ascii")
        self._handle = handle
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self.pid_path.unlink()
        except FileNotFoundError:
            pass
        if self._handle:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None

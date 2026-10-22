"""Cross-process pipeline lock and non-blocking macOS Telegram launch.

A locked file descriptor is inherited by a launched subprocess so the lock is held
continuously from the Telegram button press until the entire pipeline finishes.
Scheduled runs use the same lock, preventing concurrent clips and duplicate drafts.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_FD_ENV = "CLIPFACTORY_PIPELINE_LOCK_FD"


class PipelineBusy(RuntimeError):
    """Another scheduled or manually requested pipeline run is active."""


@contextmanager
def pipeline_lock(data_dir: Path) -> Iterator[int]:
    """Try to hold the run lock, including a verified inherited lock descriptor.

    The inherited-descriptor route lets the Telegram process hand the lock to a
    subprocess without releasing it and creating a race with the scheduler.
    """
    path = data_dir / "pipeline.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    inherited = os.environ.pop(LOCK_FD_ENV, None)
    if inherited is None:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    else:
        try:
            fd = int(inherited)
            held = os.fstat(fd)
            expected = path.stat()
            if (held.st_dev, held.st_ino) != (expected.st_dev, expected.st_ino):
                raise ValueError("lock descriptor does not match pipeline.lock")
        except (OSError, ValueError) as exc:
            raise RuntimeError("invalid inherited pipeline lock") from exc

    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PipelineBusy("Уже создаю ролики. Подожди завершения текущего запуска.") from exc
        yield fd
    finally:
        os.close(fd)


def start_pipeline(data_dir: Path, project: Path, *, limit: int = 1) -> int:
    """Start one pipeline run without blocking Telegram polling; return the PID.

    The child inherits the flock file descriptor and keeps the lock until it
    exits, including while rendering and sending clips for review.
    """
    if limit < 1:
        raise ValueError("limit must be positive")
    with pipeline_lock(data_dir) as fd:
        logs = data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env[LOCK_FD_ENV] = str(fd)
        with (logs / "manual.log").open("ab") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "clipfactory",
                    "run",
                    "--limit",
                    str(limit),
                ],
                cwd=project,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                pass_fds=(fd,),
            )
        return child.pid

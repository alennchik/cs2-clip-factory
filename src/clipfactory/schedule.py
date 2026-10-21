"""macOS autostart via launchd: the review bot always on, the pipeline on a daily schedule.

Two LaunchAgents in ~/Library/LaunchAgents:
- com.clipfactory.bot  `clipfactory review run`, started at login and restarted if it exits
- com.clipfactory.run  `clipfactory run --limit N` at the given times (a run missed while
                       the Mac was asleep starts when it wakes up)
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

BOT_LABEL = "com.clipfactory.bot"
RUN_LABEL = "com.clipfactory.run"
LABELS = (BOT_LABEL, RUN_LABEL)
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
# launchd starts jobs with a minimal PATH; ffmpeg from Homebrew lives in these folders.
BASE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

Runner = Callable[[list[str]], subprocess.CompletedProcess]


class ScheduleError(RuntimeError):
    pass


def parse_time(text: str) -> tuple[int, int]:
    match = TIME_RE.match(text.strip())
    if not match:
        raise ScheduleError(f"time must look like 12:00, got {text!r}")
    return int(match.group(1)), int(match.group(2))


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(label: str, agents_dir: Path | None = None) -> Path:
    return (agents_dir or launch_agents_dir()) / f"{label}.plist"


def _env(python: Path) -> dict[str, str]:
    return {"PATH": f"{python.parent}:{BASE_PATH}", "PYTHONUNBUFFERED": "1"}


def bot_plist(project: Path, python: Path, logs: Path) -> dict:
    return {
        "Label": BOT_LABEL,
        "ProgramArguments": [str(python), "-m", "clipfactory", "review", "run"],
        "WorkingDirectory": str(project),
        "EnvironmentVariables": _env(python),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(logs / "bot.log"),
        "StandardErrorPath": str(logs / "bot.log"),
    }


def run_plist(project: Path, python: Path, logs: Path, times: Sequence[str], limit: int) -> dict:
    if not times:
        raise ScheduleError("give at least one time, e.g. --at 12:00")
    if limit < 1:
        raise ScheduleError("limit must be at least 1")
    intervals = []
    for text in times:
        hour, minute = parse_time(text)
        intervals.append({"Hour": hour, "Minute": minute})
    return {
        "Label": RUN_LABEL,
        "ProgramArguments": [str(python), "-m", "clipfactory", "run", "--limit", str(limit)],
        "WorkingDirectory": str(project),
        "EnvironmentVariables": _env(python),
        "StartCalendarInterval": intervals,
        "StandardOutPath": str(logs / "run.log"),
        "StandardErrorPath": str(logs / "run.log"),
    }


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


@dataclass
class Scheduler:
    project: Path
    python: Path = Path(sys.executable)
    agents_dir: Path | None = None
    runner: Runner = _default_runner
    uid: int | None = None

    @property
    def domain(self) -> str:
        return f"gui/{self.uid if self.uid is not None else os.getuid()}"

    @property
    def logs(self) -> Path:
        return self.project / "data" / "logs"

    def _unload(self, label: str) -> None:
        self.runner(["launchctl", "bootout", f"{self.domain}/{label}"])  # fine if not loaded

    def _load(self, path: Path) -> None:
        proc = self.runner(["launchctl", "bootstrap", self.domain, str(path)])
        if proc.returncode != 0:
            raise ScheduleError(f"launchctl bootstrap {path.name} failed: {proc.stderr.strip()}")

    def install(self, times: Sequence[str], limit: int) -> list[Path]:
        if sys.platform != "darwin" and self.agents_dir is None:
            raise ScheduleError("autostart uses launchd and works on macOS only")
        self.logs.mkdir(parents=True, exist_ok=True)
        plists = {
            BOT_LABEL: bot_plist(self.project, self.python, self.logs),
            RUN_LABEL: run_plist(self.project, self.python, self.logs, times, limit),
        }
        written = []
        for label, data in plists.items():
            path = plist_path(label, self.agents_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._unload(label)
            with path.open("wb") as fh:
                plistlib.dump(data, fh)
            self._load(path)
            written.append(path)
        return written

    def uninstall(self) -> list[Path]:
        removed = []
        for label in LABELS:
            self._unload(label)
            path = plist_path(label, self.agents_dir)
            if path.exists():
                path.unlink()
                removed.append(path)
        return removed

    def status(self) -> dict[str, str]:
        result = {}
        for label in LABELS:
            if not plist_path(label, self.agents_dir).exists():
                result[label] = "not installed"
                continue
            proc = self.runner(["launchctl", "print", f"{self.domain}/{label}"])
            if proc.returncode != 0:
                result[label] = "installed, not loaded"
                continue
            state = re.search(r"^\s*state = (.+)$", proc.stdout, re.MULTILINE)
            result[label] = state.group(1).strip() if state else "loaded"
        return result

    def run_now(self) -> None:
        proc = self.runner(["launchctl", "kickstart", f"{self.domain}/{RUN_LABEL}"])
        if proc.returncode != 0:
            raise ScheduleError(f"could not start the run: {proc.stderr.strip()}")

import plistlib
import subprocess
from pathlib import Path

import pytest

from clipfactory.schedule import (
    BOT_LABEL,
    RUN_LABEL,
    ScheduleError,
    Scheduler,
    parse_time,
    run_plist,
)


class FakeLaunchctl:
    def __init__(self, fail_bootstrap: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail_bootstrap = fail_bootstrap

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        code = 1 if (self.fail_bootstrap and cmd[1] == "bootstrap") else 0
        out = "\tstate = running\n" if cmd[1] == "print" else ""
        return subprocess.CompletedProcess(cmd, code, out, "boom" if code else "")


def _scheduler(tmp_path: Path, runner: FakeLaunchctl) -> Scheduler:
    project = tmp_path / "project"
    project.mkdir()
    return Scheduler(
        project=project,
        python=Path("/x/.venv/bin/python"),
        agents_dir=tmp_path / "LaunchAgents",
        runner=runner,
        uid=501,
    )


def test_parse_time():
    assert parse_time("9:05") == (9, 5)
    assert parse_time("20:00") == (20, 0)
    for bad in ("24:00", "12:60", "noon", "12"):
        with pytest.raises(ScheduleError):
            parse_time(bad)


def test_run_plist_needs_times_and_limit(tmp_path):
    with pytest.raises(ScheduleError):
        run_plist(tmp_path, Path("/py"), tmp_path, [], 3)
    with pytest.raises(ScheduleError):
        run_plist(tmp_path, Path("/py"), tmp_path, ["12:00"], 0)


def test_install_writes_both_agents_and_loads_them(tmp_path):
    fake = FakeLaunchctl()
    sched = _scheduler(tmp_path, fake)
    paths = sched.install(["12:00", "20:30"], limit=4)
    assert [p.name for p in paths] == [f"{BOT_LABEL}.plist", f"{RUN_LABEL}.plist"]

    bot = plistlib.loads(paths[0].read_bytes())
    assert bot["ProgramArguments"][1:] == ["-m", "clipfactory", "review", "run"]
    assert bot["KeepAlive"] is True and bot["RunAtLoad"] is True
    assert bot["WorkingDirectory"] == str(sched.project)
    assert bot["EnvironmentVariables"]["PATH"].startswith("/x/.venv/bin:/opt/homebrew/bin")

    run = plistlib.loads(paths[1].read_bytes())
    assert run["ProgramArguments"][-3:] == ["run", "--limit", "4"]
    assert run["StartCalendarInterval"] == [{"Hour": 12, "Minute": 0}, {"Hour": 20, "Minute": 30}]
    assert run["StandardOutPath"].endswith("data/logs/run.log")
    assert (sched.project / "data" / "logs").is_dir()

    verbs = [c[1] for c in fake.calls]
    assert verbs == ["bootout", "bootstrap", "bootout", "bootstrap"]
    assert fake.calls[1][2] == "gui/501"


def test_status_and_uninstall(tmp_path):
    fake = FakeLaunchctl()
    sched = _scheduler(tmp_path, fake)
    assert sched.status() == {BOT_LABEL: "not installed", RUN_LABEL: "not installed"}
    sched.install(["12:00"], limit=3)
    assert sched.status() == {BOT_LABEL: "running", RUN_LABEL: "running"}
    removed = sched.uninstall()
    assert len(removed) == 2 and not any(p.exists() for p in removed)


def test_bootstrap_failure_is_reported(tmp_path):
    sched = _scheduler(tmp_path, FakeLaunchctl(fail_bootstrap=True))
    with pytest.raises(ScheduleError, match="boom"):
        sched.install(["12:00"], limit=3)

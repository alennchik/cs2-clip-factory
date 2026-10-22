"""Cross-process lock tests for scheduled and on-demand pipeline runs."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipfactory.run_control import LOCK_FD_ENV, PipelineBusy, pipeline_lock, start_pipeline


def test_two_runs_cannot_hold_pipeline_lock(tmp_path: Path):
    with pipeline_lock(tmp_path), pytest.raises(PipelineBusy):
        with pipeline_lock(tmp_path):
            pytest.fail("second pipeline run should never start")
    with pipeline_lock(tmp_path):
        pass  # releases cleanly after a finished run


def test_inherited_lock_survives_handoff(tmp_path: Path):
    """The scheduled-run lock can be passed to a child without a release window."""
    code = (
        "from clipfactory.run_control import pipeline_lock; "
        "from pathlib import Path; "
        "lock = pipeline_lock(Path(__import__('sys').argv[1])); "
        "fd = lock.__enter__(); "
        "assert fd >= 0; "
        "lock.__exit__(None, None, None)"
    )
    with pipeline_lock(tmp_path) as fd:
        env = os.environ.copy()
        env[LOCK_FD_ENV] = str(fd)
        proc = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            env=env,
            pass_fds=(fd,),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        with pytest.raises(PipelineBusy), pipeline_lock(tmp_path):
            pass


def test_launcher_passes_lock_to_background_child(tmp_path: Path, monkeypatch):
    calls: list[dict] = []

    def fake_popen(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        assert kwargs["pass_fds"]
        assert kwargs["env"][LOCK_FD_ENV] == str(kwargs["pass_fds"][0])
        assert kwargs["start_new_session"] is True
        with pytest.raises(PipelineBusy), pipeline_lock(tmp_path):
            pass
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr("clipfactory.run_control.subprocess.Popen", fake_popen)
    project = tmp_path / "project"
    project.mkdir()
    assert start_pipeline(tmp_path, project) == 12345
    assert calls[0]["argv"][-3:] == ["run", "--limit", "1"]
    assert calls[0]["cwd"] == project
    assert (tmp_path / "logs" / "manual.log").is_file()
    with pipeline_lock(tmp_path):
        pass


def test_invalid_limit_and_inherited_descriptor(tmp_path: Path, monkeypatch):
    with pytest.raises(ValueError, match="limit"):
        start_pipeline(tmp_path, tmp_path, limit=0)
    monkeypatch.setenv(LOCK_FD_ENV, "not-an-fd")
    with pytest.raises(RuntimeError, match="invalid inherited"), pipeline_lock(tmp_path):
        pass

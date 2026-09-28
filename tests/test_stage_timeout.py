"""Optional stage budgets, process cleanup, and continuation after failure."""

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import pytest

import hydroflow_opt.runner as runner
from hydroflow_opt import (
    EvaluationPlan,
    EvaluationStage,
    SlurmBackend,
    SubprocessBackend,
    case_from_name,
    load_config,
)
from hydroflow_opt.config import ExecutionConfig, OptimizationConfig
from tests.test_hydroflow_opt import write_config


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1800"])
def test_invalid_timeout(value):
    with pytest.raises(ValueError, match="positive integer"):
        ExecutionConfig(stage_timeout_seconds=value)


@pytest.mark.parametrize("value", ["0", "-1", "true", "1.5", '"1800"'])
def test_invalid_timeout_toml(tmp_path, value):
    path = write_config(tmp_path, backend="local")
    path.write_text(
        path.read_text().replace(
            'backend = "local"',
            f'backend = "local"\nstage_timeout_seconds = {value}',
        )
    )
    with pytest.raises(ValueError, match="positive integer"):
        load_config(path)


@pytest.mark.parametrize("timeout", [None, 1800])
def test_config_persistence_and_resume(tmp_path, monkeypatch, timeout):
    path = write_config(tmp_path, backend="local")
    if timeout is not None:
        path.write_text(
            path.read_text().replace(
                'backend = "local"',
                f'backend = "local"\nstage_timeout_seconds = {timeout}',
            )
        )
    config = replace(
        load_config(path), optimization=OptimizationConfig(1, 5, 1, seed=5)
    )
    expected = {"backend": "local"}
    if timeout is not None:
        expected["stage_timeout_seconds"] = timeout
    assert runner._effective_config(config)["execution"] == expected
    backend = SubprocessBackend(config, case_from_name("quadratic"))
    paths = backend._evaluation_paths(config.candidates[0])
    assert (
        backend._request(config.candidates[0], None, paths)["context"][
            "execution"
        ]
        == expected
    )

    seen = []

    def interrupted(config, *args):
        seen.append(config.execution.stage_timeout_seconds)
        raise RuntimeError("test interruption")

    monkeypatch.setattr(runner, "_continue_optimization", interrupted)
    with pytest.raises(RuntimeError, match="test interruption"):
        runner.run_optimization(config)
    with pytest.raises(RuntimeError, match="test interruption"):
        runner.resume_optimization(config.run_dir)
    assert seen == [timeout, timeout]


@pytest.mark.parametrize(
    "timeout,flag",
    [
        (None, None),
        (1, "--time=1"),
        (60, "--time=1"),
        (61, "--time=2"),
        (1800, "--time=30"),
    ],
)
def test_slurm_time_limit(tmp_path, monkeypatch, timeout, flag):
    config = load_config(write_config(tmp_path, backend="slurm"))
    config = replace(
        config,
        execution=replace(config.execution, stage_timeout_seconds=timeout),
    )
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setattr(
        "hydroflow_opt.backends.slurm.shutil.which", lambda _: "srun"
    )
    backend = SlurmBackend(config, case_from_name("quadratic"))
    stage = EvaluationStage("sleep", ("sleep", "120"), tmp_path)
    flags = [
        arg
        for arg in backend.launch_command(stage)
        if arg.startswith("--time=")
    ]
    assert flags == ([] if flag is None else [flag])


class TimeoutCase:
    """Hang on the first candidate; use the real toy worker thereafter."""

    def evaluation_plan(self, candidate, paths, resources):
        if candidate.parameters["x"] != 3.0:
            return case_from_name("quadratic").evaluation_plan(
                candidate, paths, resources
            )
        # Parent exits on TERM; child ignores it and keeps log files open.
        child = (
            "import os, signal, time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "Path('child.pid').write_text(str(os.getpid())); "
            "print('child ready', flush=True); time.sleep(120)"
        )
        parent = (
            "import os, subprocess, sys, time; from pathlib import Path; "
            "Path('parent.pid').write_text(str(os.getpid())); "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
            "print('solver starting', flush=True); "
            "print('partial diagnostic', file=sys.stderr, flush=True); "
            "time.sleep(120)"
        )
        return EvaluationPlan(
            (
                EvaluationStage(
                    "solve",
                    (sys.executable, "-c", parent),
                    paths.evaluation_dir,
                ),
                EvaluationStage(
                    "unreachable", ("must-not-run",), paths.scratch_dir
                ),
            )
        )


def _alive(pid):
    stat = Path(f"/proc/{pid}/stat")
    # A killed orphan can briefly remain a zombie until PID 1 reaps it.
    try:
        return stat.read_text().split(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return False


def test_local_timeout_kills_children_and_optimization_continues(tmp_path):
    config = load_config(write_config(tmp_path))
    config = replace(
        config, execution=ExecutionConfig(stage_timeout_seconds=2)
    )
    backend = SubprocessBackend(config, TimeoutCase())
    problem = runner._OptimizationProblem(
        config, "test", 0, 0, "initial", backend
    )
    evaluation = config.run_dir / "evaluations" / "island-000-initial-000"
    started = time.monotonic()
    try:
        assert problem.fitness([3.0, 4.0]) == [runner._PENALTY]
        assert time.monotonic() - started < 15
        for name in ("parent", "child"):
            pid = int((evaluation / f"{name}.pid").read_text())
            # SIGKILL delivery is asynchronous; the orphan isn't our child
            # to reap. Allow the kernel to finish its exit transition.
            deadline = time.monotonic() + 2
            while _alive(pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not _alive(pid)
        stage_dir = evaluation / "stages" / "01-solve"
        assert "child ready" in (stage_dir / "stdout.log").read_text()
        assert "partial diagnostic" in (stage_dir / "stderr.log").read_text()
        metadata = json.loads((stage_dir / "metadata.json").read_text())
        assert metadata["timed_out"] is True
        assert metadata["stage_timeout_seconds"] == 2
        result = json.loads((evaluation / "result.json").read_text())
        assert result["status"] == "failed"
        assert "exceeded time limit of 2 seconds" in result["error"]
        assert not (evaluation / "stages" / "02-unreachable").exists()
        assert problem.fitness([1.0, 1.0]) == [2.0]
    finally:
        # Keep the test safe even when process cleanup regresses.
        for name in ("parent", "child"):
            path = evaluation / f"{name}.pid"
            if path.exists():
                with suppress(ProcessLookupError):
                    os.kill(int(path.read_text()), signal.SIGKILL)


def test_slurm_failure_preserves_diagnostics_and_continues(
    tmp_path, monkeypatch
):
    config = load_config(write_config(tmp_path, backend="slurm"))
    config = replace(
        config, execution=replace(config.execution, stage_timeout_seconds=60)
    )
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setattr(
        "hydroflow_opt.backends.slurm.shutil.which", lambda _: "srun"
    )
    real_run = subprocess.run
    calls = []

    def fake_srun(command, **kwargs):
        assert "--time=1" in command
        assert "timeout" not in kwargs
        calls.append(command)
        if len(calls) == 1:
            kwargs["stderr"].write("STEP CANCELLED DUE TO TIME LIMIT\n")
            return subprocess.CompletedProcess(command, 1)
        args = command[1:]
        while args[0].startswith("--"):
            args.pop(0)
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_srun)
    backend = SlurmBackend(config, case_from_name("quadratic"))
    problem = runner._OptimizationProblem(
        config, "test", 0, 0, "initial", backend
    )
    assert problem.fitness([3.0, 4.0]) == [runner._PENALTY]
    assert problem.fitness([1.0, 1.0]) == [2.0]
    # Slurm diagnostics carry the cause; exit code alone isn't a timeout tag.
    metadata_path = next(
        config.run_dir.glob(
            "evaluations/island-000-initial-000/stages/*/metadata.json"
        )
    )
    stage_dir = metadata_path.parent
    metadata = json.loads(metadata_path.read_text())
    assert metadata["stage_timeout_seconds"] == 60
    assert "timed_out" not in metadata
    assert "TIME LIMIT" in (stage_dir / "stderr.log").read_text()

"""Explicit Slurm memory reservations and resume compatibility."""

import json
from dataclasses import replace

import pytest

import hydroflow_opt.runner as runner
from hydroflow_opt import (
    ResourceRequest,
    SlurmBackend,
    case_from_name,
    load_config,
)
from hydroflow_opt.cli import main
from hydroflow_opt.config import ExecutionConfig
from hydroflow_opt.models import BackendKind
from tests.test_hydroflow_opt import (
    InMemoryBackend,
    add_optimization,
    stop_before_evolution,
    write_config,
)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1536"])
def test_memory_rejects_invalid_python_values(value):
    with pytest.raises(ValueError, match="positive integer"):
        ResourceRequest(memory_mib_per_evaluation=value)


@pytest.mark.parametrize("value", ["0", "-1", "true", "1.5", '"1536"'])
def test_memory_rejects_invalid_toml_values(tmp_path, value):
    path = write_config(tmp_path, backend="slurm")
    path.write_text(
        path.read_text().replace(
            "memory_mib_per_evaluation = 1536",
            f"memory_mib_per_evaluation = {value}",
        )
    )
    with pytest.raises(ValueError, match="positive integer"):
        load_config(path)


def test_slurm_requires_memory_even_outside_allocation(tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    path = write_config(tmp_path, backend="slurm")
    path.write_text(
        path.read_text().replace("memory_mib_per_evaluation = 1536", "")
    )
    with pytest.raises(ValueError, match="Slurm requires"):
        main(["check", str(path)])
    local = load_config(write_config(tmp_path))
    assert local.resources.memory_mib_per_evaluation is None
    with pytest.raises(ValueError, match="Slurm requires"):
        replace(local, execution=ExecutionConfig(BackendKind.SLURM))
    configured = replace(
        local, resources=ResourceRequest(memory_mib_per_evaluation=1536)
    )
    assert configured.resources.memory_mib_per_evaluation == 1536


@pytest.mark.parametrize(
    "memory,nodes,accepted",
    [
        ("65536", "2", True),
        ("2048", "2", True),
        ("2048", "1", False),
        ("1000", "2", False),
        ("0", "2", True),
        (None, "2", True),
        ("64G", "2", True),
        ("65536", None, True),
        ("65536", "unknown", True),
    ],
)
def test_slurm_memory_capacity(tmp_path, monkeypatch, memory, nodes, accepted):
    config = load_config(write_config(tmp_path, backend="slurm", concurrent=2))
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setattr(
        "hydroflow_opt.backends.slurm.shutil.which", lambda _: "srun"
    )
    monkeypatch.delenv("SLURM_NNODES", raising=False)
    for key, value in [
        ("SLURM_MEM_PER_NODE", memory),
        ("SLURM_JOB_NUM_NODES", nodes),
    ]:
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    if accepted:
        SlurmBackend.validate_environment(config)
    else:
        with pytest.raises(RuntimeError, match="memory allocation fits"):
            runner.run_optimization(
                replace(
                    config, optimization=runner.OptimizationConfig(2, 5, 1)
                )
            )
        assert not config.run_dir.exists()


@pytest.mark.parametrize(
    "variable",
    ["SLURM_MEM_PER_NODE", "SLURM_MEM_PER_CPU", "SLURM_MEM_PER_GPU"],
)
def test_explicit_memory_overrides_inherited_requests(
    tmp_path, monkeypatch, variable
):
    config = load_config(write_config(tmp_path, backend="slurm"))
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    monkeypatch.setenv(variable, "65536")
    monkeypatch.setattr(
        "hydroflow_opt.backends.slurm.shutil.which", lambda _: "srun"
    )
    backend = SlurmBackend(config, case_from_name("quadratic"))
    plan = backend.case.evaluation_plan(
        config.candidates[0],
        backend._evaluation_paths(config.candidates[0]),
        config.resources,
    )
    command = backend.launch_command(plan.stages[0])
    assert "--mem=1536M" in command
    assert not any(
        arg.startswith(("--mem-per-cpu", "--mem-per-gpu")) for arg in command
    )


@pytest.fixture
def legacy_slurm_run(tmp_path, monkeypatch):
    pytest.importorskip("pygmo")
    path = write_config(tmp_path, backend="slurm")
    add_optimization(path, seed=42)
    config = load_config(path)
    with monkeypatch.context() as patch:
        patch.setattr(runner, "_build_archipelago", stop_before_evolution)
        with pytest.raises(RuntimeError, match="initialization complete"):
            runner.run_optimization(config, backend=InMemoryBackend())
    manifest_path = config.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["config"]["resources"]["memory_mib_per_evaluation"]
    manifest["config_hash"] = runner._json_hash(manifest["config"])
    manifest_path.write_text(json.dumps(manifest))
    return config.run_dir, manifest


def test_resume_adds_and_persists_memory_without_repeating_outcomes(
    legacy_slurm_run,
):
    run_dir, old = legacy_slurm_run
    outcomes = {
        p: p.stat().st_mtime_ns
        for p in run_dir.glob("evaluations/*/outcome.json")
    }
    summary = runner.resume_optimization(
        run_dir, backend=InMemoryBackend(), memory_mib_per_evaluation=2048
    )
    assert summary.total == 10
    assert all(p.stat().st_mtime_ns == stamp for p, stamp in outcomes.items())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["config"]["resources"]["memory_mib_per_evaluation"] == 2048
    assert manifest["config_hash"] == runner._json_hash(manifest["config"])
    assert manifest["provenance"][-1]["memory_override"] == {
        "previous_config_hash": old["config_hash"],
        "old_mib": None,
        "new_mib": 2048,
    }
    assert (
        runner._config_from_manifest(
            run_dir, manifest["config"]
        ).resources.memory_mib_per_evaluation
        == 2048
    )
    assert runner.resume_optimization(run_dir).total == 10


@pytest.mark.parametrize("budget", [None, 0, -1, True, "2048"])
def test_invalid_resume_memory_does_not_mutate_manifest(
    legacy_slurm_run, budget
):
    run_dir, _ = legacy_slurm_run
    path = run_dir / "manifest.json"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        runner.resume_optimization(
            run_dir,
            backend=InMemoryBackend(),
            memory_mib_per_evaluation=budget,
        )
    assert path.read_bytes() == before


def test_resume_memory_cannot_bypass_hash_check(legacy_slurm_run):
    run_dir, manifest = legacy_slurm_run
    manifest["config"]["resources"]["available_cpus"] = 99
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="hash"):
        runner.resume_optimization(run_dir, memory_mib_per_evaluation=2048)
    assert path.read_bytes() == before


def test_resume_environment_failure_preserves_manifest(
    legacy_slurm_run, monkeypatch
):
    run_dir, _ = legacy_slurm_run
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    path = run_dir / "manifest.json"
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="allocation"):
        runner.resume_optimization(run_dir, memory_mib_per_evaluation=2048)
    assert path.read_bytes() == before


def test_local_resume_rejects_memory_override(legacy_slurm_run):
    run_dir, manifest = legacy_slurm_run
    manifest["config"]["execution"]["backend"] = "local"
    manifest["config_hash"] = runner._json_hash(manifest["config"])
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="only supported for Slurm"):
        runner.resume_optimization(run_dir, memory_mib_per_evaluation=2048)
    assert path.read_bytes() == before


def test_cli_passes_resume_memory(tmp_path, monkeypatch):
    calls = []

    def resume(path, **kwargs):
        calls.append((path, kwargs))
        return runner.RunSummary(
            0, 0, 0, tmp_path / "results", tmp_path / "summary"
        )

    monkeypatch.setattr("hydroflow_opt.cli.resume_optimization", resume)
    assert (
        main(["resume", str(tmp_path), "--memory-mib-per-evaluation", "1536"])
        == 0
    )
    assert calls == [(tmp_path, {"memory_mib_per_evaluation": 1536})]


def test_later_resume_uses_saved_budget(legacy_slurm_run, monkeypatch):
    run_dir, _ = legacy_slurm_run
    budgets = []

    def stop_after_validation(config, *args):
        budgets.append(config.resources.memory_mib_per_evaluation)
        raise RuntimeError("stop before workers")

    monkeypatch.setattr(
        runner, "_continue_optimization", stop_after_validation
    )
    for override in (2048, None):
        with pytest.raises(RuntimeError, match="stop before workers"):
            runner.resume_optimization(
                run_dir,
                backend=InMemoryBackend(),
                memory_mib_per_evaluation=override,
            )
    assert budgets == [2048, 2048]

"""Tests that need no CFD runtime or scheduler."""

import json
from pathlib import Path

import pytest

import flow_opt.runner as runner
from flow_opt import (
    Candidate,
    EvaluationResult,
    EvaluationStatus,
    ParameterSpace,
    ResourceRequest,
    case_from_name,
    load_config,
    run_local,
)
from flow_opt.cli import main
from flow_opt.runner import (
    SubprocessBackend,
    inspect_run,
    resume_optimization,
    run_optimization,
)


class InMemoryBackend:
    """Pickle-safe backend proving optimization is not subprocess-coupled."""

    def evaluate(self, candidate, context=None):
        objective = sum(value**2 for value in candidate.parameters.values())
        return EvaluationResult.success(candidate.id, objective)


def write_config(tmp_path, *, concurrent=1, mpi_ranks=1, name="run"):
    config_path = tmp_path / f"{name}.toml"
    config_path.write_text(
        f"""[run]
directory = "{name}"
scratch_directory = "{name}-scratch"

[case]
name = "quadratic"

[resources]
available_cpus = {concurrent * mpi_ranks}
concurrent_evaluations = {concurrent}
mpi_ranks = {mpi_ranks}
threads_per_rank = 1

[[candidate]]
id = "a"
[candidate.parameters]
x = 3.0
y = 4.0

[[candidate]]
id = "b"
[candidate.parameters]
x = 1.0
y = 2.0
""",
        encoding="utf-8",
    )
    return config_path


def add_optimization(
    config_path: Path,
    *,
    islands: int = 1,
    population_size: int = 5,
    generations: int = 1,
    seed: int | None = None,
) -> None:
    seed_line = "" if seed is None else f"seed = {seed}\n"
    with config_path.open("a", encoding="utf-8") as stream:
        stream.write(
            f"""
[optimization]
islands = {islands}
population_size = {population_size}
generations = {generations}
{seed_line}"""
        )


def test_result_constructors():
    success = EvaluationResult.success("case-1", 1.5)
    assert success.status is EvaluationStatus.SUCCESS
    assert success.objective == 1.5
    failed = EvaluationResult.failed("case-2", "boom")
    assert failed.status is EvaluationStatus.FAILED
    assert failed.error == "boom"


def test_parameter_space_decodes_named_values():
    space = ParameterSpace(("alpha", "beta"), (0.0, -1.0), (2.0, 1.0))
    assert space.decode((1.0, 0.5)) == {"alpha": 1.0, "beta": 0.5}


def test_resource_request_rejects_oversubscription():
    with pytest.raises(ValueError, match="must not exceed"):
        ResourceRequest(
            available_cpus=3, concurrent_evaluations=2, mpi_ranks=2
        )


def test_quadratic_case_exposes_parameter_space():
    assert case_from_name("quadratic").parameter_space({}).names == ("x", "y")


def test_load_config(tmp_path):
    config = load_config(write_config(tmp_path, concurrent=2, mpi_ranks=2))
    assert config.run_dir == tmp_path / "run"
    assert config.case_name == "quadratic"
    assert config.resources.total_requested_cpus == 4
    assert [candidate.id for candidate in config.candidates] == ["a", "b"]


def test_run_local_writes_isolated_results(tmp_path):
    config_path = write_config(tmp_path)
    summary = run_local(load_config(config_path), config_path=config_path)
    assert (summary.total, summary.succeeded, summary.failed) == (2, 2, 0)
    records = [
        json.loads(line)
        for line in summary.results_path.read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert [record["objective"] for record in records] == [25.0, 5.0]
    assert (tmp_path / "run" / "evaluations" / "a" / "request.json").exists()
    assert inspect_run(tmp_path / "run") == summary


def test_cli_check_run_and_inspect(tmp_path, capsys):
    config_path = write_config(tmp_path)
    assert main(["check", str(config_path)]) == 0
    assert "configuration ok" in capsys.readouterr().out
    assert main(["run", str(config_path)]) == 0
    assert "run complete" in capsys.readouterr().out
    assert main(["inspect", str(tmp_path / "run")]) == 0
    assert "2/2 succeeded" in capsys.readouterr().out


def test_run_requires_candidates(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[run]
directory = "run"

[case]
name = "quadratic"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="at least one"):
        run_local(load_config(config_path))


def test_optimization_runs_a_pygmo_island_when_available(tmp_path):
    pytest.importorskip("pygmo")
    config_path = write_config(tmp_path)
    add_optimization(config_path)
    summary = run_optimization(
        load_config(config_path), config_path=config_path
    )
    assert summary.total == 10
    assert summary.failed == 0


def test_optimization_writes_reproducibility_and_final_state(tmp_path):
    pytest.importorskip("pygmo")
    config_path = write_config(tmp_path)
    add_optimization(config_path, generations=2, seed=42)

    summary = run_optimization(load_config(config_path))

    manifest = json.loads(
        (tmp_path / "run" / "manifest.json").read_text(encoding="utf-8")
    )
    checkpoint = json.loads(
        (tmp_path / "run" / "optimization" / "checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary.total == 15
    assert manifest["status"] == "complete"
    assert manifest["config"]["optimization"]["seed"] == 42
    assert checkpoint["islands"][0]["generation"] == 2
    assert len(checkpoint["history"]) == 2
    assert (tmp_path / "run" / "optimization" / "champions.json").exists()
    assert (
        tmp_path / "run" / "optimization" / "final-populations.json"
    ).exists()
    assert manifest["evaluation_ids"][0] == "island-000-initial-000"
    assert manifest["evaluation_ids"][-1] == (
        "island-000-generation-000002-trial-004"
    )


def test_omitted_seed_is_generated_and_recorded(tmp_path):
    pytest.importorskip("pygmo")
    config_path = write_config(tmp_path)
    add_optimization(config_path)
    run_optimization(load_config(config_path))
    manifest = json.loads(
        (tmp_path / "run" / "manifest.json").read_text(encoding="utf-8")
    )
    seed = manifest["config"]["optimization"]["seed"]
    assert isinstance(seed, int)
    assert 0 <= seed <= 0xFFFFFFFF


def test_new_optimization_rejects_nonempty_run_directory(tmp_path):
    pytest.importorskip("pygmo")
    config_path = write_config(tmp_path)
    add_optimization(config_path)
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "unrelated.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="not empty"):
        run_optimization(load_config(config_path))


def test_resume_continues_from_last_completed_generation(
    tmp_path, monkeypatch
):
    pytest.importorskip("pygmo")
    interrupted_path = write_config(tmp_path, name="interrupted")
    reference_path = write_config(tmp_path, name="reference")
    add_optimization(interrupted_path, generations=2, seed=123)
    add_optimization(reference_path, generations=2, seed=123)
    original_save = runner._save_checkpoint

    def save_then_interrupt(run_dir, checkpoint):
        original_save(run_dir, checkpoint)
        history = checkpoint["history"]
        if history and history[-1]["generation"] == 1:
            raise RuntimeError("simulated interruption")

    with monkeypatch.context() as patch:
        patch.setattr(runner, "_save_checkpoint", save_then_interrupt)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            run_optimization(load_config(interrupted_path))

    resumed = resume_optimization(tmp_path / "interrupted")
    reference = run_optimization(load_config(reference_path))
    assert resumed.total == reference.total == 15
    resumed_populations = json.loads(
        (
            tmp_path
            / "interrupted"
            / "optimization"
            / "final-populations.json"
        ).read_text(encoding="utf-8")
    )
    reference_populations = json.loads(
        (
            tmp_path / "reference" / "optimization" / "final-populations.json"
        ).read_text(encoding="utf-8")
    )
    assert resumed_populations == reference_populations


def test_resume_warns_for_versions_and_ignores_stale_results(tmp_path):
    pytest.importorskip("pygmo")
    config_path = write_config(tmp_path)
    add_optimization(config_path, seed=5)
    original = run_optimization(load_config(config_path))
    run_dir = tmp_path / "run"
    stale_dir = run_dir / "evaluations" / "stale"
    stale_dir.mkdir()
    (stale_dir / "result.json").write_text(
        json.dumps(
            {
                "candidate_id": "stale",
                "status": "success",
                "objective": 0.0,
            }
        ),
        encoding="utf-8",
    )
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "running"
    manifest["provenance"][-1]["packages"]["numpy"] = "older-version"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.warns(RuntimeWarning, match="packages"):
        resumed = resume_optimization(run_dir)

    assert resumed.total == original.total == 10
    updated = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert updated["provenance"][-1]["warnings"]


def test_resume_rejects_changed_parameter_space(tmp_path):
    pytest.importorskip("pygmo")
    config_path = write_config(tmp_path)
    add_optimization(config_path, seed=5)
    run_optimization(load_config(config_path))
    manifest_path = tmp_path / "run" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "running"
    manifest["parameter_space"]["lower_bounds"][0] = -6.0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="parameter names or bounds"):
        resume_optimization(tmp_path / "run")


def test_subprocess_backend_reuses_exact_terminal_result(
    tmp_path, monkeypatch
):
    config_path = write_config(tmp_path)
    config = load_config(config_path)
    backend = SubprocessBackend(config, case_from_name("quadratic"))
    candidate = config.candidates[0]
    first = backend.evaluate(candidate)

    def must_not_run(*args, **kwargs):
        raise AssertionError("cached result should avoid subprocess execution")

    monkeypatch.setattr("subprocess.run", must_not_run)
    second = backend.evaluate(candidate)
    assert second.objective == first.objective


def test_subprocess_backend_archives_mismatched_unfinished_attempt(tmp_path):
    config_path = write_config(tmp_path)
    config = load_config(config_path)
    backend = SubprocessBackend(config, case_from_name("quadratic"))
    original = config.candidates[0]
    backend.evaluate(original)

    replacement = Candidate(original.id, {"x": 1.0, "y": 1.0})
    result = backend.evaluate(replacement)

    assert result.objective == 2.0
    attempt = tmp_path / "run" / "evaluations" / "a" / "attempts"
    assert (attempt / "attempt-0001" / "request.json").exists()
    assert (attempt / "attempt-0001" / "result.json").exists()


def test_two_islands_restore_migration_database_between_generations(tmp_path):
    pytest.importorskip("pygmo")
    config_path = write_config(tmp_path, concurrent=2)
    add_optimization(config_path, islands=2, generations=2, seed=99)
    summary = run_optimization(load_config(config_path))
    assert summary.total == 30
    checkpoint = json.loads(
        (tmp_path / "run" / "optimization" / "checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(checkpoint["migrants_db"]) == 2


def test_optimization_accepts_backend_without_worker_result_files(tmp_path):
    pytest.importorskip("pygmo")
    config_path = write_config(tmp_path)
    add_optimization(config_path, seed=11)

    summary = run_optimization(
        load_config(config_path), backend=InMemoryBackend()
    )

    assert summary.total == 10
    evaluation = tmp_path / "run" / "evaluations" / "island-000-initial-000"
    assert (evaluation / "outcome.json").exists()
    assert not (evaluation / "result.json").exists()

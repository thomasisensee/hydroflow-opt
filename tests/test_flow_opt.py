"""Tests that need no CFD runtime or scheduler."""

import json

import pytest

from flow_opt import (
    EvaluationResult,
    EvaluationStatus,
    ParameterSpace,
    ResourceRequest,
    case_from_name,
    load_config,
    run_local,
)
from flow_opt.cli import main
from flow_opt.runner import inspect_run, run_optimization


def write_config(tmp_path, *, concurrent=1, mpi_ranks=1):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""[run]
directory = "run"
scratch_directory = "scratch"

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
    with config_path.open("a", encoding="utf-8") as stream:
        stream.write(
            """
[optimization]
islands = 1
population_size = 5
generations = 1
"""
        )
    summary = run_optimization(
        load_config(config_path), config_path=config_path
    )
    assert summary.total == 10
    assert summary.failed == 0

import json

import pytest

from flow_opt import (
    Candidate,
    EvaluationResult,
    EvaluationStatus,
    ExecutionContext,
    load_config,
    run_local,
)
from flow_opt.cli import main
from flow_opt.evaluators import evaluator_from_name
from flow_opt.runner import inspect_run


def write_config(tmp_path, evaluator="quadratic"):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""[run]
directory = "run"
scratch_directory = "scratch"

[evaluator]
name = "{evaluator}"

[resources]
cpus = 2

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


def test_quadratic_evaluator(tmp_path):
    evaluator = evaluator_from_name("quadratic")
    candidate = Candidate("c", {"x": 3.0, "y": 4.0})
    config = load_config(write_config(tmp_path))
    context = ExecutionContext(
        run_dir=config.run_dir,
        scratch_dir=config.scratch_dir,
        cpus=config.cpus,
    )
    result = evaluator.evaluate(candidate, context)
    assert result.status is EvaluationStatus.SUCCESS
    assert result.objective == 25.0


def test_load_config(tmp_path):
    config = load_config(write_config(tmp_path))
    assert config.run_dir == tmp_path / "run"
    assert config.scratch_dir == tmp_path / "scratch"
    assert config.evaluator == "quadratic"
    assert config.cpus == 2
    assert [candidate.id for candidate in config.candidates] == ["a", "b"]


def test_run_local_writes_results(tmp_path):
    config_path = write_config(tmp_path)
    summary = run_local(load_config(config_path), config_path=config_path)

    assert summary.total == 2
    assert summary.succeeded == 2
    assert summary.failed == 0
    assert summary.results_path.exists()
    assert summary.summary_path.exists()
    assert (tmp_path / "run" / "config.toml").exists()

    result_lines = summary.results_path.read_text(
        encoding="utf-8"
    ).splitlines()
    records = [json.loads(line) for line in result_lines]
    assert [record["objective"] for record in records] == [25.0, 5.0]

    loaded = inspect_run(tmp_path / "run")
    assert loaded == summary


def test_run_local_records_failures(tmp_path):
    config_path = write_config(tmp_path, evaluator="failing")
    summary = run_local(load_config(config_path))
    assert summary.total == 2
    assert summary.succeeded == 0
    assert summary.failed == 2


def test_load_config_rejects_empty_candidate_list(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[run]
directory = "run"

[evaluator]
name = "quadratic"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="at least one"):
        load_config(config_path)


def test_cli_check_run_and_inspect(tmp_path, capsys):
    config_path = write_config(tmp_path)

    assert main(["check", str(config_path)]) == 0
    assert "configuration ok" in capsys.readouterr().out

    assert main(["run", str(config_path)]) == 0
    assert "run complete" in capsys.readouterr().out

    assert main(["inspect", str(tmp_path / "run")]) == 0
    assert "2/2 succeeded" in capsys.readouterr().out

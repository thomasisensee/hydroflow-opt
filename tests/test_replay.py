"""Generate portable debug configs from saved evaluation requests."""

import json
import tomllib

import pytest

from hydroflow_opt.cli import main
from hydroflow_opt.config import load_config
from hydroflow_opt.replay import create_replay_config


@pytest.fixture
def request_data():
    return {
        "candidate": {
            "id": "island-042-generation-000001-trial-004",
            "parameters": {
                "x": -0.030112933317876872,
                "y": 1.4931177861833276,
            },
        },
        "case": {
            "name": "not-installed-here",
            "options": {
                "laminar_iterations": 100,
                "nested": {"enabled": True, "values": [1, 2, 3]},
                "label": 'A "quoted" value\nwith a newline and \\ backslash',
            },
        },
        "context": {
            "run_dir": "/cluster/original-run",
            "scratch_dir": "/cluster/original-scratch/candidate",
            "resources": {
                "available_cpus": 256,
                "concurrent_evaluations": 64,
                "mpi_ranks": 2,
                "threads_per_rank": 2,
                "memory_mib_per_evaluation": 1536,
            },
            "execution": {
                "backend": "slurm",
                "stage_timeout_seconds": 1800,
            },
            "optimization": {"island": 42, "generation": 1},
        },
    }


def write_request(tmp_path, data):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(data))
    return path


def test_replay_preserves_inputs_and_uses_single_candidate_resources(
    tmp_path, request_data, monkeypatch
):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    source = write_request(tmp_path, request_data)
    output_dir = tmp_path / "elsewhere"
    output_dir.mkdir()
    output = output_dir / "debug-042.toml"

    # Works without the plugin, an allocation, or an accessible source run.
    assert main(["replay-config", str(source), str(output)]) == 0
    raw = tomllib.loads(output.read_text())
    assert raw["candidate"] == [request_data["candidate"]]
    assert raw["case"] == request_data["case"]
    assert raw["execution"] == request_data["context"]["execution"]
    assert raw["resources"] == {
        "available_cpus": 4,
        "concurrent_evaluations": 1,
        "mpi_ranks": 2,
        "threads_per_rank": 2,
        "memory_mib_per_evaluation": 1536,
    }
    assert "optimization" not in raw
    config = load_config(output)
    assert config.run_dir == output_dir / "runs" / "debug-042"
    assert config.scratch_dir == config.run_dir / "scratch"
    assert not config.run_dir.exists()
    assert json.loads(source.read_text()) == request_data


def test_legacy_local_request_without_execution_or_memory(
    tmp_path, request_data
):
    del request_data["context"]["execution"]
    request_data["context"]["resources"]["memory_mib_per_evaluation"] = None
    source = write_request(tmp_path, request_data)
    output = tmp_path / "debug.toml"
    create_replay_config(source, output)
    config = load_config(output)
    assert config.execution.backend == "local"
    assert config.execution.stage_timeout_seconds is None
    assert config.resources.memory_mib_per_evaluation is None


def test_does_not_overwrite_existing_toml(tmp_path, request_data):
    source = write_request(tmp_path, request_data)
    output = tmp_path / "debug.toml"
    output.write_text("existing content")
    with pytest.raises(FileExistsError):
        create_replay_config(source, output)
    assert output.read_text() == "existing content"


def test_does_not_reuse_populated_run_directory(tmp_path, request_data):
    source = write_request(tmp_path, request_data)
    output = tmp_path / "debug.toml"
    destination = tmp_path / "runs" / "debug"
    destination.mkdir(parents=True)
    (destination / "results.jsonl").write_text("cached results")
    with pytest.raises(ValueError, match="not empty"):
        create_replay_config(source, output)
    assert not output.exists()
    assert (destination / "results.jsonl").read_text() == "cached results"


@pytest.mark.parametrize("missing", ["candidate", "case", "context"])
def test_missing_request_data_leaves_no_config(
    tmp_path, request_data, missing
):
    del request_data[missing]
    source = write_request(tmp_path, request_data)
    output = tmp_path / "debug.toml"
    with pytest.raises(ValueError, match=missing):
        create_replay_config(source, output)
    assert not output.exists()


def test_unsupported_case_option_is_not_silently_dropped(
    tmp_path, request_data
):
    request_data["case"]["options"]["explicit_null"] = None
    source = write_request(tmp_path, request_data)
    output = tmp_path / "debug.toml"
    with pytest.raises(ValueError, match="TOML cannot represent"):
        create_replay_config(source, output)
    assert not output.exists()


def test_missing_slurm_memory_is_not_guessed(tmp_path, request_data):
    request_data["context"]["resources"]["memory_mib_per_evaluation"] = None
    source = write_request(tmp_path, request_data)
    with pytest.raises(ValueError, match="Slurm requires"):
        create_replay_config(source, tmp_path / "debug.toml")


def test_cli_reports_invalid_json(tmp_path, capsys):
    source = tmp_path / "request.json"
    source.write_text("invalid json")
    with pytest.raises(SystemExit) as exc:
        main(["replay-config", str(source), str(tmp_path / "debug.toml")])
    assert exc.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_generated_config_can_run(tmp_path, request_data):
    request_data["case"] = {"name": "quadratic", "options": {}}
    request_data["context"]["execution"] = {"backend": "local"}
    request_data["context"]["resources"].update(
        mpi_ranks=1, threads_per_rank=1
    )
    source = write_request(tmp_path, request_data)
    output = tmp_path / "debug.toml"
    create_replay_config(source, output)
    assert main(["run", str(output)]) == 0
    summary = json.loads(
        (tmp_path / "runs" / "debug" / "summary.json").read_text()
    )
    assert summary["total"] == 1
    assert summary["succeeded"] == 1

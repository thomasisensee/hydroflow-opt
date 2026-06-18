"""Configuration loading for local workflow runs."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flow_opt.models import Candidate


@dataclass(frozen=True)
class FlowOptConfig:
    """Parsed configuration for a local flow-opt run.

    Parameters
    ----------
    run_dir
        Directory where durable run artifacts are written.
    scratch_dir
        Directory available for temporary evaluator output.
    evaluator
        Name of the evaluator requested by the configuration.
    candidates
        Ordered candidate list to evaluate.
    cpus
        CPU resource hint for one candidate evaluation.
    """

    run_dir: Path
    scratch_dir: Path
    evaluator: str
    candidates: tuple[Candidate, ...]
    cpus: int = 1


def load_config(path: str | Path) -> FlowOptConfig:
    """Load a TOML workflow configuration.

    Parameters
    ----------
    path
        Path to the TOML configuration file. Relative run and scratch paths are
        resolved relative to the configuration file's parent directory.

    Returns
    -------
    FlowOptConfig
        Parsed and validated configuration.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    tomllib.TOMLDecodeError
        If the file is not valid TOML.
    ValueError
        If required tables or fields are missing or malformed.
    """

    config_path = Path(path)
    with config_path.open("rb") as stream:
        raw = tomllib.load(stream)

    base_dir = config_path.parent
    run = _expect_table(raw, "run")
    evaluator = _expect_table(raw, "evaluator")
    resources = raw.get("resources", {})
    if not isinstance(resources, dict):
        raise ValueError("'resources' must be a TOML table")

    run_dir = _resolve_path(base_dir, _expect_str(run, "directory"))
    scratch_value = run.get("scratch_directory", run_dir / "scratch")
    scratch_dir = _resolve_path(base_dir, scratch_value)
    evaluator_name = _expect_str(evaluator, "name")
    cpus = int(resources.get("cpus", 1))
    if cpus < 1:
        raise ValueError("'resources.cpus' must be at least 1")

    candidates = tuple(_parse_candidates(raw.get("candidate", [])))
    if not candidates:
        raise ValueError("at least one [[candidate]] entry is required")

    return FlowOptConfig(
        run_dir=run_dir,
        scratch_dir=scratch_dir,
        evaluator=evaluator_name,
        candidates=candidates,
        cpus=cpus,
    )


def _parse_candidates(raw: Any) -> list[Candidate]:
    if not isinstance(raw, list):
        raise ValueError("'candidate' must be an array of TOML tables")

    candidates: list[Candidate] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError("each candidate must be a TOML table")
        candidate_id = str(item.get("id", f"candidate-{index}"))
        parameters = item.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("'candidate.parameters' must be a table")
        candidates.append(
            Candidate(
                id=candidate_id,
                parameters={
                    str(key): float(value) for key, value in parameters.items()
                },
            )
        )
    return candidates


def _expect_table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"'{name}' must be a TOML table")
    return value


def _expect_str(raw: dict[str, Any], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{name}' must be a non-empty string")
    return value


def _resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path

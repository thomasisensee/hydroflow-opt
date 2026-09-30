"""Create an editable single-candidate TOML from an evaluation request."""

import json
from pathlib import Path
from typing import Any

import tomlkit

from hydroflow_opt.config import _parse_config


def create_replay_config(request_path: Path, output_path: Path) -> Path:
    """Write a validated replay config without launching or loading a case.

    Run and scratch paths are relative to the new TOML. Existing files and
    populated run directories are never overwritten or reused.
    """
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("request.json must contain an object")
    candidate = _table(request, "candidate")
    if not isinstance(candidate.get("id"), str) or not candidate["id"]:
        raise ValueError("request candidate.id must be a non-empty string")
    parameters = _table(candidate, "parameters")
    case = _table(request, "case")
    context = _table(request, "context")
    resources = dict(_table(context, "resources"))
    execution = context.get("execution", {"backend": "local"})
    for name in ("mpi_ranks", "threads_per_rank"):
        value = resources.get(name, 1)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"request resources.{name} must be positive int")
        resources[name] = value
    resources["available_cpus"] = (
        resources["mpi_ranks"] * resources["threads_per_rank"]
    )
    resources["concurrent_evaluations"] = 1
    # Older/local requests serialize an unset memory reservation as null.
    resources = {
        key: value for key, value in resources.items() if value is not None
    }
    run_dir = f"runs/{output_path.stem}"
    raw = {
        "run": {
            "directory": run_dir,
            "scratch_directory": f"{run_dir}/scratch",
        },
        "case": case,
        "execution": execution,
        "resources": resources,
        "candidate": [{"id": candidate["id"], "parameters": parameters}],
    }
    config = _parse_config(raw, output_path.resolve().parent)
    if config.run_dir.exists() and (
        not config.run_dir.is_dir() or any(config.run_dir.iterdir())
    ):
        raise ValueError(
            f"replay run directory is not empty: {config.run_dir}; "
            "choose a different output TOML filename"
        )
    try:
        content = tomlkit.dumps(raw)
    except TypeError as exc:
        raise ValueError(
            f"request contains values TOML cannot represent: {exc}"
        ) from exc
    with output_path.open("x", encoding="utf-8") as stream:
        stream.write(
            "# Generated from an evaluation request. Review before running.\n"
            "# Paths are relative to this TOML; Slurm needs an allocation.\n"
            + content
        )
    return output_path


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"request {name} must be an object")
    return value

"""Structured evaluation-result persistence shared by runners and backends."""

import json
import os
import shutil
from pathlib import Path
from typing import Any

from hydroflow_opt.models import (
    EvaluationResult,
    EvaluationStatus,
)


def result_from_json(
    raw: dict[str, Any],
    candidate_id: str,
    evaluation_dir: Path,
    *,
    extra_metadata: dict[str, Any] | None = None,
) -> EvaluationResult:
    """Validate and normalize a case-produced result."""
    if raw.get("candidate_id") != candidate_id:
        raise ValueError("case result candidate_id does not match request")
    metadata_value = raw.get("metadata", {})
    if not isinstance(metadata_value, dict):
        raise TypeError("case result metadata must be an object")
    timings_value = raw.get("timings", {})
    if not isinstance(timings_value, dict):
        raise TypeError("case result timings must be an object")
    metadata_copy = dict(metadata_value)
    metadata_copy["evaluation_dir"] = str(evaluation_dir)
    if extra_metadata:
        metadata_copy.update(extra_metadata)
    if "status" not in raw:
        raise ValueError("case result is missing status")
    status = EvaluationStatus(str(raw["status"]))
    timings = {str(key): float(value) for key, value in timings_value.items()}
    if status is EvaluationStatus.SUCCESS:
        if raw.get("objective") is None:
            raise ValueError("successful case result is missing objective")
        return EvaluationResult.success(
            candidate_id,
            float(raw["objective"]),
            timings=timings,
            metadata=metadata_copy,
        )
    return EvaluationResult.failed(
        candidate_id,
        str(raw.get("error", "case reported failure")),
        timings=timings,
        metadata=metadata_copy,
    )


def write_result(path: Path, result: EvaluationResult) -> None:
    """Atomically write a normalized terminal result."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(result_to_json(result), indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def result_to_json(result: EvaluationResult) -> dict[str, object]:
    """Convert an evaluation result to its JSON representation."""
    return {
        "candidate_id": result.candidate_id,
        "status": result.status.value,
        "objective": result.objective,
        "timings": result.timings,
        "metadata": result.metadata,
        "error": result.error,
    }


def archive_attempt(evaluation_dir: Path) -> None:
    """Move files belonging to a stale evaluation attempt aside."""
    attempts_dir = evaluation_dir / "attempts"
    attempts_dir.mkdir(exist_ok=True)
    number = len([path for path in attempts_dir.iterdir() if path.is_dir()])
    destination = attempts_dir / f"attempt-{number + 1:04d}"
    destination.mkdir()
    for name in (
        "evaluation.json",
        "outcome.json",
        "request.json",
        "result.json",
        "stages",
        "stdout.log",
        "stderr.log",
    ):
        source = evaluation_dir / name
        if source.exists():
            shutil.move(str(source), destination / name)

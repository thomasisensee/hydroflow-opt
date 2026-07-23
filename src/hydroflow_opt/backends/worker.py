"""Shared JSON worker protocol for execution backends."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hydroflow_opt.cases import CasePlugin
from hydroflow_opt.config import FlowOptConfig
from hydroflow_opt.models import (
    Candidate,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
)


class WorkerBackend(ABC):
    """Execute case worker commands through a backend-specific launcher."""

    def __init__(self, config: FlowOptConfig, case: CasePlugin) -> None:
        self.config = config
        self.case = case

    def evaluate(
        self,
        candidate: Candidate,
        context: EvaluationContext | None = None,
    ) -> EvaluationResult:
        """Run one worker and convert worker/protocol errors to results."""

        evaluation_dir = self.config.run_dir / "evaluations" / candidate.id
        scratch_dir = self.config.scratch_dir / candidate.id
        evaluation_dir.mkdir(parents=True, exist_ok=True)
        scratch_dir.mkdir(parents=True, exist_ok=True)
        request_path = evaluation_dir / "request.json"
        result_path = evaluation_dir / "result.json"
        request = {
            "candidate": asdict(candidate),
            "case": {
                "name": self.config.case_name,
                "options": self.config.case_options,
            },
            "context": {
                "run_dir": str(self.config.run_dir),
                "scratch_dir": str(scratch_dir),
                "resources": asdict(self.config.resources),
                "optimization": asdict(context) if context else None,
            },
        }
        cached = self._cached_result(
            request, request_path, result_path, evaluation_dir
        )
        if cached is not None:
            return cached

        request_path.write_text(
            json.dumps(request, indent=2) + "\n", encoding="utf-8"
        )
        environment = os.environ.copy()
        environment["OMP_NUM_THREADS"] = str(
            self.config.resources.threads_per_rank
        )
        worker_command = self.case.worker_command(request_path, result_path)
        completed = subprocess.run(
            self.launch_command(worker_command),
            check=False,
            cwd=evaluation_dir,
            env=environment,
            capture_output=True,
            text=True,
        )
        (evaluation_dir / "stdout.log").write_text(
            completed.stdout, encoding="utf-8"
        )
        (evaluation_dir / "stderr.log").write_text(
            completed.stderr, encoding="utf-8"
        )
        if completed.returncode != 0:
            result = EvaluationResult.failed(
                candidate.id,
                f"worker exited with status {completed.returncode}",
                metadata=self.execution_metadata(evaluation_dir),
            )
            write_result(result_path, result)
            return result
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            result = result_from_json(
                raw,
                candidate.id,
                evaluation_dir,
                extra_metadata=self.execution_metadata(evaluation_dir),
            )
            write_result(result_path, result)
            return result
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result = EvaluationResult.failed(
                candidate.id,
                f"invalid worker result: {exc}",
                metadata=self.execution_metadata(evaluation_dir),
            )
            write_result(result_path, result)
            return result

    @abstractmethod
    def launch_command(self, worker_command: list[str]) -> list[str]:
        """Return the complete command used to launch one worker."""

    def execution_metadata(self, evaluation_dir: Path) -> dict[str, Any]:
        """Return backend metadata attached to the normalized result."""

        return {"evaluation_dir": str(evaluation_dir)}

    def _cached_result(
        self,
        request: dict[str, Any],
        request_path: Path,
        result_path: Path,
        evaluation_dir: Path,
    ) -> EvaluationResult | None:
        if not request_path.exists():
            return None
        try:
            previous_request = json.loads(
                request_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            archive_attempt(evaluation_dir)
            return None
        if previous_request != request:
            archive_attempt(evaluation_dir)
            return None
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            return result_from_json(
                raw, str(request["candidate"]["id"]), evaluation_dir
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            archive_attempt(evaluation_dir)
            return None


def result_from_json(
    raw: dict[str, Any],
    candidate_id: str,
    evaluation_dir: Path,
    *,
    extra_metadata: dict[str, Any] | None = None,
) -> EvaluationResult:
    """Validate and normalize a worker result."""

    if raw.get("candidate_id") != candidate_id:
        raise ValueError("worker result candidate_id does not match request")
    metadata_value = raw.get("metadata", {})
    if not isinstance(metadata_value, dict):
        raise TypeError("worker result metadata must be an object")
    metadata_copy = dict(metadata_value)
    metadata_copy["evaluation_dir"] = str(evaluation_dir)
    if extra_metadata:
        metadata_copy.update(extra_metadata)
    status = EvaluationStatus(str(raw["status"]))
    if status is EvaluationStatus.SUCCESS:
        return EvaluationResult.success(
            candidate_id,
            float(raw["objective"]),
            timings={
                str(key): float(value)
                for key, value in raw.get("timings", {}).items()
            },
            metadata=metadata_copy,
        )
    return EvaluationResult.failed(
        candidate_id,
        str(raw.get("error", "worker reported failure")),
        timings={
            str(key): float(value)
            for key, value in raw.get("timings", {}).items()
        },
        metadata=metadata_copy,
    )


def write_result(path: Path, result: EvaluationResult) -> None:
    """Write a normalized terminal worker result."""

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
        "stdout.log",
        "stderr.log",
    ):
        source = evaluation_dir / name
        if source.exists():
            shutil.move(str(source), destination / name)

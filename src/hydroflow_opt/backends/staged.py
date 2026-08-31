"""Shared orchestration for ordered, resource-aware evaluation stages."""

import json
import os
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hydroflow_opt.cases import CasePlugin
from hydroflow_opt.config import FlowOptConfig
from hydroflow_opt.models import (
    Candidate,
    EvaluationContext,
    EvaluationPaths,
    EvaluationResult,
    EvaluationStage,
    EvaluationStatus,
)
from hydroflow_opt.results import (
    archive_attempt,
    result_from_json,
    write_result,
)


@dataclass(frozen=True)
class _StageRun:
    """Recorded outcome of one stage launch."""

    returncode: int | None
    duration_seconds: float
    error: str | None = None


class StagedBackend(ABC):
    """Evaluate candidates as ordered commands supplied by a case plugin."""

    def __init__(self, config: FlowOptConfig, case: CasePlugin) -> None:
        """Initialize the backend for one configuration and case."""
        self.config = config
        self.case = case

    def evaluate(
        self,
        candidate: Candidate,
        context: EvaluationContext | None = None,
    ) -> EvaluationResult:
        """Execute a case plan and normalize its terminal result."""
        paths = self._evaluation_paths(candidate)
        paths.evaluation_dir.mkdir(parents=True, exist_ok=True)
        paths.scratch_dir.mkdir(parents=True, exist_ok=True)
        request = self._request(candidate, context, paths)
        cached = self._cached_result(request, paths)
        if cached is not None:
            return cached

        paths.request_path.write_text(
            json.dumps(request, indent=2) + "\n", encoding="utf-8"
        )
        plan = self.case.evaluation_plan(
            candidate, paths, self.config.resources
        )
        self._validate_plan_resources(plan.stages)

        timings: dict[str, float] = {}
        for index, stage in enumerate(plan.stages, start=1):
            stage_dir = (
                paths.evaluation_dir / "stages" / f"{index:02d}-{stage.name}"
            )
            run = self._run_stage(stage, stage_dir)
            timings[stage.name] = run.duration_seconds
            if run.error is not None:
                return self._failed_result(
                    candidate,
                    paths,
                    timings,
                    f"stage '{stage.name}' could not start: {run.error}",
                    stage.name,
                )
            if run.returncode != 0:
                return self._failed_result(
                    candidate,
                    paths,
                    timings,
                    f"stage '{stage.name}' exited with status "
                    f"{run.returncode}",
                    stage.name,
                )

        try:
            raw = json.loads(paths.result_path.read_text(encoding="utf-8"))
            result = result_from_json(
                raw,
                candidate.id,
                paths.evaluation_dir,
                extra_metadata=self.execution_metadata(paths.evaluation_dir),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._failed_result(
                candidate,
                paths,
                timings,
                f"invalid case result: {exc}",
                None,
            )

        normalized = _with_timings(result, timings)
        write_result(paths.result_path, normalized)
        return normalized

    @abstractmethod
    def launch_command(self, stage: EvaluationStage) -> list[str]:
        """Translate a portable stage into a backend-specific command."""

    def execution_metadata(self, evaluation_dir: Path) -> dict[str, Any]:
        """Return backend metadata attached to the normalized result."""
        return {
            "evaluation_dir": str(evaluation_dir),
            "stages_directory": str(evaluation_dir / "stages"),
        }

    def _evaluation_paths(self, candidate: Candidate) -> EvaluationPaths:
        evaluation_dir = self.config.run_dir / "evaluations" / candidate.id
        return EvaluationPaths(
            evaluation_dir=evaluation_dir,
            scratch_dir=self.config.scratch_dir / candidate.id,
            request_path=evaluation_dir / "request.json",
            result_path=evaluation_dir / "result.json",
        )

    def _request(
        self,
        candidate: Candidate,
        context: EvaluationContext | None,
        paths: EvaluationPaths,
    ) -> dict[str, Any]:
        return {
            "candidate": asdict(candidate),
            "case": {
                "name": self.config.case_name,
                "options": self.config.case_options,
            },
            "context": {
                "run_dir": str(self.config.run_dir),
                "scratch_dir": str(paths.scratch_dir),
                "resources": asdict(self.config.resources),
                "execution": {"backend": self.config.execution.backend.value},
                "optimization": asdict(context) if context else None,
            },
        }

    def _cached_result(
        self, request: dict[str, Any], paths: EvaluationPaths
    ) -> EvaluationResult | None:
        if not paths.request_path.exists():
            return None
        try:
            previous_request = json.loads(
                paths.request_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            archive_attempt(paths.evaluation_dir)
            return None
        if previous_request != request:
            archive_attempt(paths.evaluation_dir)
            return None
        try:
            raw = json.loads(paths.result_path.read_text(encoding="utf-8"))
            return result_from_json(
                raw,
                request["candidate"]["id"],
                paths.evaluation_dir,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            archive_attempt(paths.evaluation_dir)
            return None

    def _validate_plan_resources(
        self, stages: tuple[EvaluationStage, ...]
    ) -> None:
        available = self.config.resources.cpus_per_evaluation
        for stage in stages:
            if stage.resources.cpus > available:
                raise ValueError(
                    f"stage '{stage.name}' requests {stage.resources.cpus} "
                    f"CPUs, but one evaluation may use at most {available}"
                )

    def _run_stage(self, stage: EvaluationStage, stage_dir: Path) -> _StageRun:
        stage_dir.mkdir(parents=True, exist_ok=True)
        command = self.launch_command(stage)
        environment = os.environ.copy()
        environment["OMP_NUM_THREADS"] = str(
            stage.resources.threads_per_process
        )
        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                check=False,
                cwd=stage.working_directory,
                env=environment,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            duration = time.perf_counter() - started
            (stage_dir / "stdout.log").write_text("", encoding="utf-8")
            (stage_dir / "stderr.log").write_text(str(exc), encoding="utf-8")
            run = _StageRun(None, duration, str(exc))
        else:
            duration = time.perf_counter() - started
            (stage_dir / "stdout.log").write_text(
                completed.stdout, encoding="utf-8"
            )
            (stage_dir / "stderr.log").write_text(
                completed.stderr, encoding="utf-8"
            )
            run = _StageRun(completed.returncode, duration)
        metadata = {
            "name": stage.name,
            "command": list(stage.command),
            "launch_command": command,
            "working_directory": str(stage.working_directory),
            "resources": asdict(stage.resources),
            "started_at": started_at,
            "duration_seconds": run.duration_seconds,
            "returncode": run.returncode,
            "error": run.error,
        }
        (stage_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        return run

    def _failed_result(
        self,
        candidate: Candidate,
        paths: EvaluationPaths,
        timings: dict[str, float],
        error: str,
        failed_stage: str | None,
    ) -> EvaluationResult:
        metadata = self.execution_metadata(paths.evaluation_dir)
        if failed_stage is not None:
            metadata["failed_stage"] = failed_stage
        result = EvaluationResult.failed(
            candidate.id,
            error,
            timings=timings,
            metadata=metadata,
        )
        write_result(paths.result_path, result)
        return result


def _with_timings(
    result: EvaluationResult, stage_timings: dict[str, float]
) -> EvaluationResult:
    timings = {**result.timings, **stage_timings}
    if result.status is EvaluationStatus.SUCCESS:
        assert result.objective is not None
        return EvaluationResult.success(
            result.candidate_id,
            result.objective,
            timings=timings,
            metadata=result.metadata,
        )
    return EvaluationResult.failed(
        result.candidate_id,
        result.error or "case reported failure",
        timings=timings,
        metadata=result.metadata,
    )

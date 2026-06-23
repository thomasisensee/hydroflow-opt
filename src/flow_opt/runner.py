"""Local subprocess execution and pygmo island optimization."""

import json
import os
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from flow_opt.cases import CasePlugin, case_from_name
from flow_opt.config import FlowOptConfig
from flow_opt.models import (
    Candidate,
    EvaluationResult,
    EvaluationStatus,
)


@dataclass(frozen=True)
class RunSummary:
    """Aggregate information about a completed run."""

    total: int
    succeeded: int
    failed: int
    results_path: Path
    summary_path: Path


class SubprocessBackend:
    """Run isolated case workers within a fixed resource budget."""

    def __init__(self, config: FlowOptConfig, case: CasePlugin) -> None:
        self.config = config
        self.case = case
        self._slots = threading.BoundedSemaphore(
            config.resources.concurrent_evaluations
        )

    def evaluate(self, candidate: Candidate) -> EvaluationResult:
        """Run one worker and convert all protocol errors to results."""

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
            },
        }
        request_path.write_text(
            json.dumps(request, indent=2), encoding="utf-8"
        )
        environment = os.environ.copy()
        environment["OMP_NUM_THREADS"] = str(
            self.config.resources.threads_per_rank
        )
        with self._slots:
            completed = subprocess.run(
                self.case.worker_command(request_path, result_path),
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
            return EvaluationResult.failed(
                candidate.id,
                f"worker exited with status {completed.returncode}",
                metadata={"evaluation_dir": str(evaluation_dir)},
            )
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            return _result_from_json(raw, candidate.id, evaluation_dir)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return EvaluationResult.failed(
                candidate.id,
                f"invalid worker result: {exc}",
                metadata={"evaluation_dir": str(evaluation_dir)},
            )


def run_local(
    config: FlowOptConfig, *, config_path: Path | None = None
) -> RunSummary:
    """Evaluate explicit candidates with the configured isolated worker."""

    if not config.candidates:
        raise ValueError("'run' requires at least one [[candidate]] entry")
    _prepare_run(config, config_path)
    backend = SubprocessBackend(config, case_from_name(config.case_name))
    with ThreadPoolExecutor(
        max_workers=config.resources.concurrent_evaluations
    ) as executor:
        results = list(executor.map(backend.evaluate, config.candidates))
    return _write_summary(config, results)


def run_optimization(
    config: FlowOptConfig, *, config_path: Path | None = None
) -> RunSummary:
    """Optimize a case using the standard pygmo DE island model."""

    if config.optimization is None:
        raise ValueError("'optimize' requires an [optimization] table")
    if config.optimization.islands > config.resources.concurrent_evaluations:
        raise ValueError(
            "optimization.islands must not exceed "
            "resources.concurrent_evaluations"
        )
    try:
        import pygmo as pg
    except (
        ImportError
    ) as exc:  # pragma: no cover - dependency is required in releases.
        raise RuntimeError(
            "pygmo is required for 'flow-opt optimize'"
        ) from exc

    _prepare_run(config, config_path)
    optimization = config.optimization
    algorithm = pg.algorithm(
        pg.de(
            gen=optimization.generations,
            F=optimization.differential_weight,
            CR=optimization.crossover_rate,
        )
    )
    archipelago = pg.archipelago(t=pg.fully_connected())
    for _ in range(optimization.islands):
        population = pg.population(
            pg.problem(_OptimizationProblem(config)),
            optimization.population_size,
        )
        archipelago.push_back(
            pg.island(udi=pg.mp_island(), algo=algorithm, pop=population)
        )
    archipelago.evolve()
    archipelago.wait_check()
    return _write_summary(config, _read_worker_results(config))


class _OptimizationProblem:
    """Pickle-safe pygmo UDP that delegates one fitness call to flow-opt."""

    def __init__(self, config: FlowOptConfig) -> None:
        self.config = config
        self.space = case_from_name(config.case_name).parameter_space(
            config.case_options
        )

    def fitness(self, vector: list[float]) -> list[float]:
        candidate = Candidate(
            id=f"evaluation-{uuid.uuid4().hex}",
            parameters=self.space.decode(tuple(vector)),
        )
        result = SubprocessBackend(
            self.config,
            case_from_name(self.config.case_name),
        ).evaluate(candidate)
        return [result.objective if result.objective is not None else 1.0e12]

    def get_bounds(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        return self.space.lower_bounds, self.space.upper_bounds


def inspect_run(run_dir: str | Path) -> RunSummary:
    """Read a completed run summary from disk."""

    summary_path = Path(run_dir) / "summary.json"
    raw = json.loads(summary_path.read_text(encoding="utf-8"))
    return RunSummary(
        total=int(raw["total"]),
        succeeded=int(raw["succeeded"]),
        failed=int(raw["failed"]),
        results_path=Path(raw["results_path"]),
        summary_path=Path(raw["summary_path"]),
    )


def _prepare_run(config: FlowOptConfig, config_path: Path | None) -> None:
    config.run_dir.mkdir(parents=True, exist_ok=True)
    config.scratch_dir.mkdir(parents=True, exist_ok=True)
    if config_path is not None:
        shutil.copy2(config_path, config.run_dir / "config.toml")


def _write_summary(
    config: FlowOptConfig, results: list[EvaluationResult]
) -> RunSummary:
    results_path = config.run_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as stream:
        for result in results:
            stream.write(json.dumps(_result_to_json(result)) + "\n")
    succeeded = sum(
        result.status is EvaluationStatus.SUCCESS for result in results
    )
    summary = RunSummary(
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results_path=results_path,
        summary_path=config.run_dir / "summary.json",
    )
    summary.summary_path.write_text(
        json.dumps(asdict(summary), default=str, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _read_worker_results(config: FlowOptConfig) -> list[EvaluationResult]:
    results: list[EvaluationResult] = []
    for result_path in sorted(
        (config.run_dir / "evaluations").glob("*/result.json")
    ):
        raw = json.loads(result_path.read_text(encoding="utf-8"))
        results.append(
            _result_from_json(
                raw, str(raw["candidate_id"]), result_path.parent
            )
        )
    return results


def _result_from_json(
    raw: dict[str, Any], candidate_id: str, evaluation_dir: Path
) -> EvaluationResult:
    if raw.get("candidate_id") != candidate_id:
        raise ValueError("worker result candidate_id does not match request")
    metadata = dict(raw.get("metadata", {}))
    metadata["evaluation_dir"] = str(evaluation_dir)
    status = EvaluationStatus(str(raw["status"]))
    if status is EvaluationStatus.SUCCESS:
        return EvaluationResult.success(
            candidate_id,
            float(raw["objective"]),
            timings={
                str(k): float(v) for k, v in raw.get("timings", {}).items()
            },
            metadata=metadata,
        )
    return EvaluationResult.failed(
        candidate_id,
        str(raw.get("error", "worker reported failure")),
        timings={str(k): float(v) for k, v in raw.get("timings", {}).items()},
        metadata=metadata,
    )


def _result_to_json(result: EvaluationResult) -> dict[str, object]:
    return {
        "candidate_id": result.candidate_id,
        "status": result.status.value,
        "objective": result.objective,
        "timings": result.timings,
        "metadata": result.metadata,
        "error": result.error,
    }

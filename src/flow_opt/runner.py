"""Local workflow execution."""

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from flow_opt.config import FlowOptConfig
from flow_opt.evaluators import evaluator_from_name
from flow_opt.models import (
    EvaluationResult,
    EvaluationStatus,
    ExecutionContext,
)


@dataclass(frozen=True)
class RunSummary:
    """Aggregate information about a completed local run.

    Parameters
    ----------
    total
        Number of evaluated candidates.
    succeeded
        Number of candidates with ``SUCCESS`` status.
    failed
        Number of candidates with ``FAILED`` status.
    results_path
        Path to the JSON Lines file containing one result record per candidate.
    summary_path
        Path to the JSON summary file for the run.
    """

    total: int
    succeeded: int
    failed: int
    results_path: Path
    summary_path: Path


def run_local(
    config: FlowOptConfig,
    *,
    config_path: Path | None = None,
) -> RunSummary:
    """Run configured candidates sequentially in the local process.

    The local runner creates the configured run and scratch directories,
    evaluates each candidate with the configured evaluator, writes one JSONL
    result record per candidate, and writes a run summary. Evaluator exceptions
    are converted into failed ``EvaluationResult`` records so that one failed
    candidate does not abort the whole run.

    Parameters
    ----------
    config
        Parsed workflow configuration.
    config_path
        Optional path to the source configuration file. If provided, the
        file is copied into the run directory as ``config.toml``.

    Returns
    -------
    RunSummary
        Aggregate run counts and paths to generated result files.

    Raises
    ------
    ValueError
        If the configured evaluator name is unknown.
    OSError
        If required run, scratch, or result files cannot be created.
    """

    config.run_dir.mkdir(parents=True, exist_ok=True)
    config.scratch_dir.mkdir(parents=True, exist_ok=True)
    if config_path is not None:
        shutil.copy2(config_path, config.run_dir / "config.toml")

    context = ExecutionContext(
        run_dir=config.run_dir,
        scratch_dir=config.scratch_dir,
        cpus=config.cpus,
    )
    evaluator = evaluator_from_name(config.evaluator)
    results: list[EvaluationResult] = []
    results_path = config.run_dir / "results.jsonl"

    with results_path.open("w", encoding="utf-8") as stream:
        for candidate in config.candidates:
            try:
                result = evaluator.evaluate(candidate, context)
            except Exception as exc:  # noqa: BLE001 - runner owns isolation.
                result = EvaluationResult.failed(candidate.id, str(exc))
            results.append(result)
            stream.write(json.dumps(_result_to_json(result)) + "\n")

    summary = _summarize(
        results, results_path, config.run_dir / "summary.json"
    )
    with summary.summary_path.open("w", encoding="utf-8") as stream:
        json.dump(asdict(summary), stream, default=str, indent=2)
        stream.write("\n")
    return summary


def inspect_run(run_dir: str | Path) -> RunSummary:
    """Read a completed run summary from disk.

    Parameters
    ----------
    run_dir
        Directory containing a ``summary.json`` file written by ``run_local``.

    Returns
    -------
    RunSummary
        Parsed summary record.

    Raises
    ------
    FileNotFoundError
        If ``summary.json`` does not exist in ``run_dir``.
    json.JSONDecodeError
        If the summary file is not valid JSON.
    KeyError
        If the summary file does not contain the expected fields.
    """

    summary_path = Path(run_dir) / "summary.json"
    with summary_path.open(encoding="utf-8") as stream:
        raw = json.load(stream)
    return RunSummary(
        total=int(raw["total"]),
        succeeded=int(raw["succeeded"]),
        failed=int(raw["failed"]),
        results_path=Path(raw["results_path"]),
        summary_path=Path(raw["summary_path"]),
    )


def _summarize(
    results: list[EvaluationResult],
    results_path: Path,
    summary_path: Path,
) -> RunSummary:
    succeeded = sum(
        result.status is EvaluationStatus.SUCCESS for result in results
    )
    failed = len(results) - succeeded
    return RunSummary(
        total=len(results),
        succeeded=succeeded,
        failed=failed,
        results_path=results_path,
        summary_path=summary_path,
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

"""Core data structures for workflow evaluation."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, Self


class EvaluationStatus(str, Enum):
    """Terminal state of one candidate evaluation.

    Values
    ------
    SUCCESS
        The evaluator completed and produced an objective value.
    FAILED
        The evaluator did not produce a valid objective value. The result
        should contain an error message explaining the failure.
    """

    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True)
class Candidate:
    """Input parameters for one workflow evaluation.

    Parameters
    ----------
    id
        Stable identifier for the candidate. It is used in logs and result
        files, so it should be unique within one run.
    parameters
        Numeric parameter values passed to the evaluator. The meaning of each
        parameter is defined by the workflow-specific evaluator.
    """

    id: str
    parameters: dict[str, float]


@dataclass(frozen=True)
class ExecutionContext:
    """Runtime paths and resource hints passed to evaluators.

    Parameters
    ----------
    run_dir
        Directory for durable run artifacts such as copied configuration,
        result records, summaries, and logs.
    scratch_dir
        Directory for temporary evaluator output. Evaluators may use this for
        generated meshes, solver cases, or intermediate files.
    cpus
        CPU count requested for one candidate evaluation. This is a resource
        hint for evaluators and local backends, not a scheduler contract.
    metadata
        Additional backend- or site-specific context values. The core package
        stores the mapping but does not interpret it.
    """

    run_dir: Path
    scratch_dir: Path
    cpus: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationResult:
    """Result record produced by one candidate evaluation.

    Parameters
    ----------
    candidate_id
        Identifier of the evaluated candidate.
    status
        Terminal evaluation status.
    objective
        Scalar objective value. Successful evaluations should set this value;
        failed evaluations usually leave it as ``None``.
    timings
        Mapping from stage names to elapsed seconds, for example
        ``{"meshing": 3.2, "simulation": 41.0}``.
    metadata
        Additional structured result data produced by the evaluator.
    error
        Human-readable error message for failed evaluations.
    """

    candidate_id: str
    status: EvaluationStatus
    objective: float | None = None
    timings: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def success(
        cls,
        candidate_id: str,
        objective: float,
        *,
        timings: dict[str, float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Self:
        """Create a successful evaluation result.

        Parameters
        ----------
        candidate_id
            Identifier of the evaluated candidate.
        objective
            Scalar objective value produced by the evaluator.
        timings
            Optional stage timings in seconds.
        metadata
            Optional additional structured result data.

        Returns
        -------
        EvaluationResult
            A result with ``status`` set to ``SUCCESS``.
        """

        return cls(
            candidate_id=candidate_id,
            status=EvaluationStatus.SUCCESS,
            objective=objective,
            timings=timings or {},
            metadata=metadata or {},
        )

    @classmethod
    def failed(
        cls,
        candidate_id: str,
        error: str,
        *,
        timings: dict[str, float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Self:
        """Create a failed evaluation result.

        Parameters
        ----------
        candidate_id
            Identifier of the evaluated candidate.
        error
            Human-readable description of the failure.
        timings
            Optional stage timings collected before the failure.
        metadata
            Optional additional structured diagnostic data.

        Returns
        -------
        EvaluationResult
            A result with ``status`` set to ``FAILED``.
        """

        return cls(
            candidate_id=candidate_id,
            status=EvaluationStatus.FAILED,
            timings=timings or {},
            metadata=metadata or {},
            error=error,
        )


class CaseEvaluator(Protocol):
    """Interface implemented by workflow-specific evaluators.

    Evaluators contain the case-specific work: creating inputs, running a
    simulation or surrogate, computing an objective, and returning structured
    diagnostics. The core runner only requires this protocol and therefore does
    not depend on dtOO, OpenFOAM, Slurm, or a specific optimizer.
    """

    def evaluate(
        self,
        candidate: Candidate,
        context: ExecutionContext,
    ) -> EvaluationResult:
        """Evaluate one candidate.

        Parameters
        ----------
        candidate
            Candidate parameter set to evaluate.
        context
            Runtime paths and resource hints for this evaluation.

        Returns
        -------
        EvaluationResult
            Structured result record for the candidate.
        """

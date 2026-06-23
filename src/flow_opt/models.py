"""Public contracts for cases, resources, and evaluation results."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Self


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
class ParameterSpace:
    """Named, bounded numerical input space exposed by an optimization case."""

    names: tuple[str, ...]
    lower_bounds: tuple[float, ...]
    upper_bounds: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.names:
            raise ValueError(
                "a parameter space must contain at least one value"
            )
        if not (
            len(self.names) == len(self.lower_bounds) == len(self.upper_bounds)
        ):
            raise ValueError(
                "parameter names and bounds must have equal length"
            )
        if len(set(self.names)) != len(self.names):
            raise ValueError("parameter names must be unique")
        if any(
            low >= high
            for low, high in zip(
                self.lower_bounds, self.upper_bounds, strict=True
            )
        ):
            raise ValueError(
                "each lower bound must be smaller than its upper bound"
            )

    def decode(
        self, values: list[float] | tuple[float, ...]
    ) -> dict[str, float]:
        """Map an optimizer vector to the case's named parameter mapping."""

        if len(values) != len(self.names):
            raise ValueError(
                "candidate dimension does not match parameter space"
            )
        return {
            name: float(value)
            for name, value in zip(self.names, values, strict=True)
        }


@dataclass(frozen=True)
class ResourceRequest:
    """The complete CPU shape of one evaluation and its local capacity."""

    available_cpus: int = 1
    concurrent_evaluations: int = 1
    mpi_ranks: int = 1
    threads_per_rank: int = 1

    def __post_init__(self) -> None:
        values = (
            self.available_cpus,
            self.concurrent_evaluations,
            self.mpi_ranks,
            self.threads_per_rank,
        )
        if any(value < 1 for value in values):
            raise ValueError("resource counts must be at least one")
        if self.total_requested_cpus > self.available_cpus:
            raise ValueError(
                "concurrent_evaluations * mpi_ranks * threads_per_rank "
                "must not exceed available_cpus"
            )

    @property
    def cpus_per_evaluation(self) -> int:
        """CPU count occupied by one candidate evaluation."""

        return self.mpi_ranks * self.threads_per_rank

    @property
    def total_requested_cpus(self) -> int:
        """Maximum simultaneous CPU demand."""

        return self.concurrent_evaluations * self.cpus_per_evaluation


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

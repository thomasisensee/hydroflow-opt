"""Built-in evaluators used for tests and examples."""

import time

from flow_opt.models import (
    Candidate,
    CaseEvaluator,
    EvaluationResult,
    ExecutionContext,
)


class QuadraticEvaluator:
    """Deterministic toy objective for local tests.

    The evaluator computes the sum of squared parameter values. It is intended
    for testing the orchestration layer without relying on external simulation
    software.
    """

    def evaluate(
        self,
        candidate: Candidate,
        context: ExecutionContext,
    ) -> EvaluationResult:
        """Evaluate a candidate with a quadratic objective.

        Parameters
        ----------
        candidate
            Candidate whose numeric parameter values are squared and summed.
        context
            Execution context for the evaluation. The evaluator records the
            requested CPU count in the result metadata.

        Returns
        -------
        EvaluationResult
            Successful result containing the quadratic objective and a single
            ``evaluation`` timing entry.
        """

        start = time.perf_counter()
        objective = sum(
            value * value for value in candidate.parameters.values()
        )
        elapsed = time.perf_counter() - start
        return EvaluationResult.success(
            candidate.id,
            objective,
            timings={"evaluation": elapsed},
            metadata={"cpus": context.cpus},
        )


class FailingEvaluator:
    """Evaluator that always returns a failed result.

    This evaluator is useful for tests that need deterministic failure records
    without triggering Python exceptions.
    """

    def evaluate(
        self,
        candidate: Candidate,
        context: ExecutionContext,
    ) -> EvaluationResult:
        """Return an intentional failure for one candidate.

        Parameters
        ----------
        candidate
            Candidate to mark as failed.
        context
            Execution context for the evaluation. It is accepted for protocol
            compatibility and otherwise unused.

        Returns
        -------
        EvaluationResult
            Failed result with a deterministic error message.
        """

        del context
        return EvaluationResult.failed(candidate.id, "intentional failure")


def evaluator_from_name(name: str) -> CaseEvaluator:
    """Create a built-in evaluator by name.

    Parameters
    ----------
    name
        Built-in evaluator name. Supported values are ``"quadratic"`` and
        ``"failing"``.

    Returns
    -------
    CaseEvaluator
        Evaluator instance matching ``name``.

    Raises
    ------
    ValueError
        If ``name`` does not identify a known built-in evaluator.
    """

    match name:
        case "quadratic":
            return QuadraticEvaluator()
        case "failing":
            return FailingEvaluator()
        case _:
            raise ValueError(f"unknown evaluator: {name}")

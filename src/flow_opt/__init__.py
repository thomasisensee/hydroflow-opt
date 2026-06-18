"""Reusable orchestration primitives for simulation-based optimization."""

from importlib import metadata

from flow_opt.config import FlowOptConfig, load_config
from flow_opt.evaluators import evaluator_from_name
from flow_opt.models import (
    Candidate,
    EvaluationResult,
    EvaluationStatus,
    ExecutionContext,
)
from flow_opt.runner import RunSummary, run_local

try:
    __version__ = metadata.version(__package__)
except metadata.PackageNotFoundError:  # pragma: no cover
    __version__ = "0+unknown"
del metadata

__all__ = [
    "Candidate",
    "EvaluationResult",
    "EvaluationStatus",
    "ExecutionContext",
    "FlowOptConfig",
    "RunSummary",
    "evaluator_from_name",
    "load_config",
    "run_local",
]

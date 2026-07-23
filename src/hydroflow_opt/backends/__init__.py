"""Built-in worker execution backends."""

from __future__ import annotations

from hydroflow_opt.backends.local import SubprocessBackend
from hydroflow_opt.backends.slurm import SlurmBackend
from hydroflow_opt.cases import CasePlugin
from hydroflow_opt.config import FlowOptConfig
from hydroflow_opt.models import BackendKind, EvaluationBackend


def backend_from_config(
    config: FlowOptConfig, case: CasePlugin
) -> EvaluationBackend:
    """Construct the built-in backend selected by effective configuration."""

    if config.execution.backend is BackendKind.LOCAL:
        return SubprocessBackend(config, case)
    return SlurmBackend(config, case)


def validate_execution_environment(config: FlowOptConfig) -> None:
    """Validate runtime prerequisites for the selected built-in backend."""

    if config.execution.backend is BackendKind.SLURM:
        SlurmBackend.validate_environment()


__all__ = [
    "SlurmBackend",
    "SubprocessBackend",
    "backend_from_config",
    "validate_execution_environment",
]

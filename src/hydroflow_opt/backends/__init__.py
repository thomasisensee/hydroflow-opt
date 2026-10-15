"""Built-in worker execution backends."""

from hydroflow_opt.backends.local import SubprocessBackend
from hydroflow_opt.backends.slurm import SlurmBackend
from hydroflow_opt.cases import CasePlugin
from hydroflow_opt.config import FlowOptConfig
from hydroflow_opt.models import BackendKind, EvaluationBackend


def backend_from_config(
    config: FlowOptConfig, case: CasePlugin
) -> EvaluationBackend:
    """Construct the built-in backend selected by effective configuration."""
    if config.scratch_mode == "node_local":
        raise RuntimeError(
            "node_local scratch requires a node_local_session; "
            "use run_local, run_optimization, or resume_optimization"
        )
    if config.execution.backend is BackendKind.LOCAL:
        return SubprocessBackend(config, case)
    return SlurmBackend(config, case)


def validate_execution_environment(config: FlowOptConfig) -> None:
    """Validate runtime prerequisites for the selected built-in backend."""
    if config.execution.backend is BackendKind.SLURM:
        SlurmBackend.validate_environment(config)
        if config.scratch_mode == "node_local":
            from hydroflow_opt.backends.placement import allocation_slots

            allocation_slots(config)


__all__ = [
    "SlurmBackend",
    "SubprocessBackend",
    "backend_from_config",
    "validate_execution_environment",
]

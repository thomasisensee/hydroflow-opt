"""Slurm job-step execution within an existing allocation."""

import os
import shutil
from pathlib import Path
from typing import Any

from hydroflow_opt.backends.staged import StagedBackend
from hydroflow_opt.config import (
    FlowOptConfig,
    scratch_directory_variables,
)
from hydroflow_opt.models import EvaluationStage


class SlurmBackend(StagedBackend):
    """Place every evaluation stage in an explicit Slurm job step."""

    @staticmethod
    def validate_environment(config: FlowOptConfig | None = None) -> None:
        """Require an existing allocation and an available ``srun`` command."""
        if "SLURM_JOB_ID" not in os.environ:
            raise RuntimeError(
                "Slurm execution requires an existing sbatch or salloc "
                "allocation (SLURM_JOB_ID is not set)"
            )
        if shutil.which("srun") is None:
            raise RuntimeError(
                "Slurm execution requires the 'srun' executable on PATH"
            )
        if config is not None and "TMPDIR" in scratch_directory_variables(
            config
        ):
            raw_nodes = os.environ.get(
                "SLURM_JOB_NUM_NODES", os.environ.get("SLURM_NNODES")
            )
            try:
                nodes = int(raw_nodes) if raw_nodes is not None else None
            except ValueError as exc:
                raise RuntimeError(
                    "Slurm $TMPDIR scratch requires a valid "
                    "SLURM_JOB_NUM_NODES value"
                ) from exc
            if nodes != 1:
                raise RuntimeError(
                    "Slurm $TMPDIR scratch requires exactly one allocated "
                    "node (SLURM_JOB_NUM_NODES=1)"
                )

        if config is not None:
            memory = config.resources.memory_mib_per_evaluation
            if memory is None:
                raise ValueError("Slurm requires memory_mib_per_evaluation")
            raw_memory = os.environ.get("SLURM_MEM_PER_NODE", "")
            raw_nodes = os.environ.get(
                "SLURM_JOB_NUM_NODES", os.environ.get("SLURM_NNODES", "")
            )
            if raw_memory.isdecimal() and raw_nodes.isdecimal():
                per_node, nodes = int(raw_memory), int(raw_nodes)
                if per_node > 0 and nodes > 0:
                    capacity = nodes * (per_node // memory)
                    if capacity < config.resources.concurrent_evaluations:
                        raise RuntimeError(
                            f"Slurm memory allocation fits only {capacity} "
                            f"concurrent evaluations at {memory} MiB each; "
                            "request more memory or reduce concurrency/budget"
                        )

    def launch_command(self, stage: EvaluationStage) -> list[str]:
        """Translate a portable stage into an exclusive one-node job step."""
        self.validate_environment(self.config)
        resources = stage.resources
        command = [
            "srun",
            "--exclusive",
            "--nodes=1",
            f"--ntasks={resources.processes}",
            f"--cpus-per-task={resources.threads_per_process}",
            "--cpu-bind=cores",
            f"--mem={self.config.resources.memory_mib_per_evaluation}M",
        ]
        if resources.processes > 1:
            command.append("--mpi=pmix")
        return [*command, *stage.command]

    def execution_metadata(self, evaluation_dir: Path) -> dict[str, Any]:
        """Record the allocation that executed the worker."""
        metadata = super().execution_metadata(evaluation_dir)
        metadata["slurm_job_id"] = os.environ["SLURM_JOB_ID"]
        return metadata

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
        ]
        if resources.processes > 1:
            command.append("--mpi=pmix")
        return [*command, *stage.command]

    def execution_metadata(self, evaluation_dir: Path) -> dict[str, Any]:
        """Record the allocation that executed the worker."""
        metadata = super().execution_metadata(evaluation_dir)
        metadata["slurm_job_id"] = os.environ["SLURM_JOB_ID"]
        return metadata

"""Slurm job-step execution within an existing allocation."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from hydroflow_opt.backends.worker import WorkerBackend
from hydroflow_opt.cases import case_worker_placement
from hydroflow_opt.models import WorkerPlacement


class SlurmBackend(WorkerBackend):
    """Place complete workers or their scheduler-aware stages with Slurm."""

    @staticmethod
    def validate_environment() -> None:
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

    def launch_command(self, worker_command: list[str]) -> list[str]:
        """Place a complete worker unless it controls scheduled stages."""

        self.validate_environment()
        if case_worker_placement(self.case) is WorkerPlacement.CONTROLLER:
            return worker_command
        cpus = self.config.resources.cpus_per_evaluation
        return [
            "srun",
            "--exclusive",
            "--nodes=1",
            "--ntasks=1",
            f"--cpus-per-task={cpus}",
            *worker_command,
        ]

    def execution_context(self) -> dict[str, Any]:
        """Supply a one-node Slurm launcher for an MPI worker stage."""

        resources = self.config.resources
        return {
            "backend": "slurm",
            "mpi_launcher": [
                "srun",
                "--exclusive",
                "--nodes=1",
                f"--ntasks={resources.mpi_ranks}",
                f"--cpus-per-task={resources.threads_per_rank}",
                "--cpu-bind=cores",
                "--mpi=pmix",
            ],
        }

    def execution_metadata(self, evaluation_dir: Path) -> dict[str, Any]:
        """Record the allocation that executed the worker."""

        metadata = super().execution_metadata(evaluation_dir)
        metadata["slurm_job_id"] = os.environ["SLURM_JOB_ID"]
        return metadata

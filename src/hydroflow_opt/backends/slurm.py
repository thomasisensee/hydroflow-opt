"""Slurm job-step execution within an existing allocation."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from hydroflow_opt.backends.worker import WorkerBackend


class SlurmBackend(WorkerBackend):
    """Run one complete case worker as one exclusive Slurm job step."""

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
        """Wrap exactly one worker in a one-node Slurm job step."""

        self.validate_environment()
        cpus = self.config.resources.cpus_per_evaluation
        return [
            "srun",
            "--exclusive",
            "--nodes=1",
            "--ntasks=1",
            f"--cpus-per-task={cpus}",
            *worker_command,
        ]

    def execution_metadata(self, evaluation_dir: Path) -> dict[str, Any]:
        """Record the allocation that executed the worker."""

        metadata = super().execution_metadata(evaluation_dir)
        metadata["slurm_job_id"] = os.environ["SLURM_JOB_ID"]
        return metadata

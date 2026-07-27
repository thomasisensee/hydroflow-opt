"""Direct local subprocess execution."""

from typing import Any

from hydroflow_opt.backends.worker import WorkerBackend


class SubprocessBackend(WorkerBackend):
    """Run isolated case workers as local subprocesses."""

    def launch_command(self, worker_command: list[str]) -> list[str]:
        """Launch the case worker without a scheduler wrapper."""

        return worker_command

    def execution_context(self) -> dict[str, Any]:
        """Use the local MPI launcher for parallel worker stages."""

        ranks = self.config.resources.mpi_ranks
        launcher = ["mpiexec", "-n", str(ranks)] if ranks > 1 else []
        return {
            "backend": "local",
            "mpi_launcher": launcher,
        }

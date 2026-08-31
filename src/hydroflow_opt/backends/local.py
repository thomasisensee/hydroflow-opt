"""Direct local stage execution."""

from hydroflow_opt.backends.staged import StagedBackend
from hydroflow_opt.models import EvaluationStage


class SubprocessBackend(StagedBackend):
    """Run case stages directly or through a local MPI launcher."""

    def launch_command(self, stage: EvaluationStage) -> list[str]:
        """Translate a stage into a direct or MPI-launched command."""
        command = list(stage.command)
        if stage.resources.processes == 1:
            return command
        return [
            "mpiexec",
            "-n",
            str(stage.resources.processes),
            *command,
        ]

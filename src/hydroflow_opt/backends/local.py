"""Direct local subprocess execution."""

from hydroflow_opt.backends.worker import WorkerBackend


class SubprocessBackend(WorkerBackend):
    """Run isolated case workers as local subprocesses."""

    def launch_command(self, worker_command: list[str]) -> list[str]:
        """Launch the case worker without a scheduler wrapper."""

        return worker_command

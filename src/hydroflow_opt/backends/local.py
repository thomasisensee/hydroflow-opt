"""Direct local stage execution."""

import os
import signal
import subprocess
import time
from contextlib import suppress
from typing import TextIO

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

    def _execute_stage(
        self,
        command: list[str],
        stage: EvaluationStage,
        environment: dict[str, str],
        stdout: TextIO,
        stderr: TextIO,
    ) -> int:
        """Bound local execution and clean up the entire process group."""
        timeout = self.config.execution.stage_timeout_seconds
        if timeout is None:
            return super()._execute_stage(
                command, stage, environment, stdout, stderr
            )
        with subprocess.Popen(
            command,
            cwd=stage.working_directory,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        ) as process:
            try:
                return process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _signal_group(process.pid, signal.SIGTERM)
                # A launcher may exit before its children. Give the whole
                # group the grace period, then kill any surviving children.
                time.sleep(5)
                _signal_group(process.pid, signal.SIGKILL)
                process.wait()
                raise


def _signal_group(process_group: int, sig: signal.Signals) -> None:
    """Signal a process group, tolerating processes that already exited."""
    with suppress(ProcessLookupError):
        os.killpg(process_group, sig)

"""Candidate-affine Slurm execution with scratch owned by a remote node."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, TextIO

from hydroflow_opt.backends.placement import (
    PlacementConnection,
    placement_session,
)
from hydroflow_opt.backends.remote import atomic_json
from hydroflow_opt.backends.slurm import SlurmBackend
from hydroflow_opt.backends.staged import _StageRun
from hydroflow_opt.cases import CasePlugin
from hydroflow_opt.config import FlowOptConfig
from hydroflow_opt.models import (
    Candidate,
    EvaluationContext,
    EvaluationPaths,
    EvaluationResult,
    EvaluationStage,
    EvaluationStatus,
)
from hydroflow_opt.results import (
    archive_attempt,
    result_from_json,
)

_HELPER = (sys.executable, "-m", "hydroflow_opt.backends.remote")


@contextmanager
def node_local_session(
    config: FlowOptConfig,
    case: CasePlugin,
) -> Iterator["NodeLocalBackend"]:
    """Share one coordinator across all candidate workers."""
    SlurmBackend.validate_environment(config)
    with placement_session(config) as connection:
        config.run_dir.mkdir(parents=True, exist_ok=True)
        shared = config.run_dir / "execution-sessions" / connection.invocation
        shared.mkdir(parents=True)
        (shared / "token").write_text(connection.invocation)
        allocator = connection.connect()
        handlers: dict[int, Any] = {}

        def interrupt(signum: int, frame: Any) -> None:
            allocator.abort(f"controller received signal {signum}")
            if signum == signal.SIGINT:
                raise KeyboardInterrupt
            raise SystemExit(128 + signum)

        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.signal(signum, interrupt)
        try:
            yield NodeLocalBackend(config, case, connection)
        finally:
            for saved_signal, handler in handlers.items():
                signal.signal(saved_signal, handler)
            leases, launches = allocator.active()
            if leases or launches:
                allocator.abort("execution session is shutting down")
                deadline = time.monotonic() + 30
                while leases and time.monotonic() < deadline:
                    time.sleep(0.2)
                    leases, launches = allocator.active()
                # A killed worker cannot clean up its own remote processes.
                job = os.environ["SLURM_JOB_ID"]
                for name in launches:
                    for step in _Attempt._owned_steps(job, name):
                        subprocess.run(
                            ["scancel", "--signal=KILL", step],
                            check=True,
                            timeout=15,
                            capture_output=True,
                        )
                    if _Attempt._owned_steps(job, name):
                        raise RuntimeError(
                            "remote steps remain active at session shutdown"
                        )


class NodeLocalBackend:
    """Pickle-safe facade; mutable placement belongs to individual attempts."""

    def __init__(
        self,
        config: FlowOptConfig,
        case: CasePlugin,
        connection: PlacementConnection,
    ) -> None:
        """Keep only immutable settings and an explicit IPC descriptor."""
        self.config = config
        self.case = case
        self.connection = connection

    def evaluate(
        self,
        candidate: Candidate,
        context: EvaluationContext | None = None,
    ) -> EvaluationResult:
        """Reuse logical outcomes or lease one node for the whole candidate."""
        directory = self.config.run_dir / "evaluations" / candidate.id
        directory.mkdir(parents=True, exist_ok=True)
        identity = {
            "candidate": asdict(candidate),
            "case": self.config.case_name,
            "options": self.config.case_options,
            "resources": asdict(self.config.resources),
            "execution": self.config.execution.as_dict(),
            "context": asdict(context) if context else None,
            "scratch_mode": "node_local",
            "scratch_template": self.config.scratch_dir_template,
        }
        identity_path = directory / "node-local-identity.json"
        if identity_path.exists():
            try:
                previous = json.loads(identity_path.read_text())
                if previous == identity:
                    return result_from_json(
                        json.loads((directory / "result.json").read_text()),
                        candidate.id,
                        directory,
                    )
            except (OSError, ValueError, TypeError, KeyError):
                pass
        if identity_path.exists() or (directory / "request.json").exists():
            # The optimizer has already written this invocation's identity.
            evaluation = directory / "evaluation.json"
            current = evaluation.read_bytes() if evaluation.exists() else None
            archive_attempt(directory)
            if current is not None and context is not None:
                evaluation.write_bytes(current)
        atomic_json(identity_path, identity)
        allocator = self.connection.connect()
        lease, node = allocator.acquire(os.getpid())
        attempt = _Attempt(
            self.config,
            self.case,
            self.connection,
            allocator,
            node,
            candidate,
            directory,
        )
        try:
            try:
                attempt.bootstrap()
                result = attempt.evaluate(candidate, context)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                result = attempt._failed_result(
                    candidate,
                    attempt.paths,
                    {},
                    str(exc),
                    "bootstrap-or-execution",
                )
            if result.status is EvaluationStatus.FAILED:
                attempt.diagnostics()
            else:
                attempt.cleanup()
            return result
        finally:
            # Aborted allocators reject all acquisitions, even after release.
            allocator.release(lease)


class _Attempt(SlurmBackend):
    """One candidate's placement; never shared with another evaluation."""

    def __init__(
        self,
        config: FlowOptConfig,
        case: CasePlugin,
        connection: PlacementConnection,
        allocator: Any,
        node: str,
        candidate: Candidate,
        directory: Path,
    ) -> None:
        super().__init__(config, case)
        self.connection = connection
        self.allocator = allocator
        self.node = node
        self.candidate = candidate
        self.directory = directory
        self.attempt = uuid.uuid4().hex
        self.placement: dict[str, Any] = {
            "assigned_node": node,
            "attempt_id": self.attempt,
            "invocation_id": connection.invocation,
        }
        # This placeholder is never created or accessed as scratch.
        self.paths = EvaluationPaths(
            directory,
            directory / "unresolved-node-local-scratch",
            directory / "request.json",
            directory / "result.json",
        )

    def bootstrap(self) -> None:
        shared = (
            self.config.run_dir
            / "execution-sessions"
            / self.connection.invocation
        )
        output = self.directory / "bootstrap.json"
        self._operation(
            "bootstrap",
            {
                "shared": str(shared),
                "token": self.connection.invocation,
                "template": self.config.scratch_dir_template
                or str(self.config.scratch_dir),
                "invocation": self.connection.invocation,
                "candidate": self.candidate.id,
                "attempt": self.attempt,
                "output": str(output),
            },
        )
        data = json.loads(output.read_text())
        if data["node"] != self.node:
            raise ValueError(
                "bootstrap ran on a node different from its assignment"
            )
        self.placement.update(data)
        self.paths = replace(self.paths, scratch_dir=Path(data["scratch_dir"]))
        atomic_json(self.directory / "placement.json", self.placement)

    def _prepare_scratch(self, paths: EvaluationPaths) -> None:
        pass  # Created by bootstrap on the owning node.

    def _evaluation_paths(self, candidate: Candidate) -> EvaluationPaths:
        return self.paths

    def _cached_result(
        self,
        request: dict[str, Any],
        paths: EvaluationPaths,
    ) -> EvaluationResult | None:
        return None  # Logical caching happens before placement is acquired.

    def execution_metadata(self, evaluation_dir: Path) -> dict[str, Any]:
        metadata = super().execution_metadata(evaluation_dir)
        metadata.update(self.placement)
        return metadata

    def launch_command(self, stage: EvaluationStage) -> list[str]:
        command = super().launch_command(stage)
        return [
            command[0],
            f"--nodelist={self.node}",
            f"--chdir={stage.working_directory}",
            f"--job-name=hf-{uuid.uuid4().hex}",
            *command[1:],
        ]

    def _run_stage(self, stage: EvaluationStage, stage_dir: Path) -> _StageRun:
        wrapped = replace(
            stage,
            command=(
                *_HELPER,
                "exec",
                str(stage_dir),
                *stage.command,
            ),
        )
        run = super()._run_stage(wrapped, stage_dir)
        metadata_path = stage_dir / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["command"] = list(stage.command)
        metadata.update(self.placement)
        placements = [
            json.loads(path.read_text())
            for path in sorted(stage_dir.glob("placement-rank-*.json"))
        ]
        metadata["rank_placements"] = placements
        if run.returncode == 0 and (
            len(placements) != stage.resources.processes
            or any(p["node"] != self.node for p in placements)
        ):
            run = replace(
                run, error="missing or mismatched remote rank placement"
            )
            metadata["error"] = run.error
        atomic_json(metadata_path, metadata)
        return run

    def _execute_stage(
        self,
        command: list[str],
        stage: EvaluationStage,
        environment: dict[str, str],
        stdout: TextIO,
        stderr: TextIO,
    ) -> int:
        self.allocator.check()
        # Helpers carry a 120-second bound independently of solver timeouts.
        helper = stage.command[:3] == _HELPER and stage.command[3] in {
            "bootstrap",
            "collect",
            "cleanup",
        }
        timeout = (
            120 if helper else self.config.execution.stage_timeout_seconds
        )
        # Slurm limits use minutes; allow time for its normal teardown.
        wall_timeout = (
            timeout
            if helper
            else (
                ((timeout + 59) // 60) * 60 + 60
                if timeout is not None
                else None
            )
        )
        name = next(
            arg.split("=", 1)[1]
            for arg in command
            if arg.startswith("--job-name=")
        )
        self.allocator.register_launch(name)
        confirmed = False
        try:
            with subprocess.Popen(
                command,
                cwd=self.directory,
                env=environment,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            ) as process:
                deadline = (
                    time.monotonic() + wall_timeout
                    if wall_timeout is not None
                    else None
                )
                try:
                    while True:
                        self.allocator.check()
                        if (
                            deadline is not None
                            and time.monotonic() >= deadline
                        ):
                            raise subprocess.TimeoutExpired(
                                command, wall_timeout or 0
                            )
                        try:
                            result = process.wait(timeout=1)
                            confirmed = True
                            return result
                        except subprocess.TimeoutExpired:
                            pass
                except BaseException:
                    self._terminate(process, command)
                    confirmed = True
                    raise
        except OSError:
            confirmed = True  # The launcher could not be started.
            raise
        finally:
            if confirmed:
                self.allocator.finish_launch(name)

    def _terminate(
        self, process: subprocess.Popen[Any], command: list[str]
    ) -> None:
        """Cancel only this launcher's steps; quarantine uncertain cleanup."""
        job = os.environ["SLURM_JOB_ID"]
        name = next(
            arg.split("=", 1)[1]
            for arg in command
            if arg.startswith("--job-name=")
        )
        try:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
            # A dead launcher alone does not prove its remote tasks are dead.
            steps = self._owned_steps(job, name)
            for step in steps:
                subprocess.run(
                    ["scancel", "--signal=KILL", step],
                    check=True,
                    timeout=15,
                    capture_output=True,
                )
            process.wait(timeout=15)
            if self._owned_steps(job, name):
                raise RuntimeError(
                    "Slurm steps remain active after cancellation"
                )
        except BaseException as exc:
            if process.poll() is None:
                process.kill()
                process.wait()
            self.allocator.abort(
                f"remote termination could not be confirmed: {exc}"
            )
            raise RuntimeError("node-local invocation quarantined") from exc

    @staticmethod
    def _owned_steps(job: str, name: str) -> list[str]:
        output = subprocess.check_output(
            [
                "squeue",
                "--steps",
                f"--jobs={job}",
                "--noheader",
                "--format=%i|%j",
            ],
            text=True,
            timeout=15,
        )
        return [
            step
            for line in output.splitlines()
            for step, _, label in [line.strip().partition("|")]
            if label == name and step.startswith(f"{job}.")
        ]

    def _operation(self, operation: str, spec: dict[str, Any]) -> None:
        directory = self.directory / "remote-operations" / operation
        directory.mkdir(parents=True, exist_ok=True)
        spec_path = directory / "spec.json"
        atomic_json(spec_path, spec)
        stage = EvaluationStage(
            f"remote-{operation}",
            (*_HELPER, operation, str(spec_path)),
            self.directory,
        )
        with (
            (directory / "stdout.log").open("w") as stdout,
            (directory / "stderr.log").open("w") as stderr,
        ):
            command = [
                arg
                for arg in self.launch_command(stage)
                if not arg.startswith("--time=")
            ]
            command.insert(1, "--time=2")
            returncode = self._execute_stage(
                command,
                stage,
                os.environ.copy(),
                stdout,
                stderr,
            )
        if returncode:
            raise subprocess.CalledProcessError(returncode, stage.command)

    def diagnostics(self) -> None:
        record: dict[str, Any] = {}
        try:
            if "scratch_dir" not in self.placement:
                raise ValueError("scratch bootstrap did not complete")
            self._operation(
                "collect",
                {
                    "scratch": str(self.paths.scratch_dir),
                    "attempt": self.attempt,
                    "output": str(self.directory / "diagnostics.tar.gz"),
                },
            )
            record["status"] = "exported"
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            record.update(status="unavailable", error=str(exc))
        atomic_json(self.directory / "diagnostics.json", record)

    def cleanup(self) -> None:
        try:
            self._operation(
                "cleanup",
                {
                    "scratch": str(self.paths.scratch_dir),
                    "attempt": self.attempt,
                },
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # Objective is already durable; cleanup does not invalidate it.
            atomic_json(self.directory / "cleanup.json", {"error": str(exc)})

"""Invocation-scoped, process-safe candidate placement within Slurm."""

import multiprocessing
import os
import re
import secrets
import subprocess
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from multiprocessing.managers import BaseManager
from typing import Any

from hydroflow_opt.config import FlowOptConfig


def allocation_slots(config: FlowOptConfig) -> dict[str, int]:
    """Read allocation budgets, never the nodes' total hardware capacity."""
    if os.environ.get("SLURM_HET_SIZE") or os.environ.get("SLURM_HET_GROUP"):
        raise ValueError(
            "node_local scratch does not support heterogeneous jobs"
        )
    nodes = subprocess.check_output(
        ["scontrol", "show", "hostnames", os.environ["SLURM_JOB_NODELIST"]],
        text=True,
        timeout=30,
    ).split()
    cpus: list[int] = []
    for group in os.environ.get("SLURM_JOB_CPUS_PER_NODE", "").split(","):
        match = re.fullmatch(r"([1-9]\d*)(?:\(x([1-9]\d*)\))?", group)
        if match is None:
            raise ValueError("invalid or missing SLURM_JOB_CPUS_PER_NODE")
        cpus.extend([int(match[1])] * int(match[2] or 1))
    if not nodes or len(nodes) != len(cpus) or len(set(nodes)) != len(nodes):
        raise ValueError("Slurm node and CPU allocation counts do not match")
    memory_node = os.environ.get("SLURM_MEM_PER_NODE", "")
    memory_cpu = os.environ.get("SLURM_MEM_PER_CPU", "")
    if memory_node.isdecimal() and int(memory_node) > 0:
        memory = [int(memory_node)] * len(nodes)
    elif not memory_node and memory_cpu.isdecimal() and int(memory_cpu) > 0:
        memory = [int(memory_cpu) * count for count in cpus]
    else:
        raise ValueError(
            "node_local scratch requires explicit Slurm memory budgets"
        )
    resources = config.resources
    assert resources.memory_mib_per_evaluation is not None
    slots = {
        node: min(
            count // resources.cpus_per_evaluation,
            mib // resources.memory_mib_per_evaluation,
        )
        for node, count, mib in zip(nodes, cpus, memory, strict=True)
    }
    if resources.available_cpus > sum(cpus):
        raise ValueError("available_cpus exceeds the Slurm allocation")
    if sum(slots.values()) < resources.concurrent_evaluations:
        raise ValueError(
            "Slurm allocation cannot fit requested candidate concurrency"
        )
    return slots


class Allocator:
    """Reserve candidate slots across independent controller processes."""

    def __init__(self, slots: dict[str, int], limit: int) -> None:
        """Initialize an allocation-wide pool with a global concurrency cap."""
        self.slots = dict(slots)
        self.nodes = list(slots)
        self.limit = limit
        self.leases: dict[str, tuple[str, int]] = {}
        self.condition = threading.Condition()
        self.cursor = 0
        self.error: str | None = None
        self.launches: set[str] = set()

    def acquire(self, owner: int) -> tuple[str, str]:
        """Wait for capacity and reserve it until explicit release."""
        with self.condition:
            while True:
                self._check()
                if len(self.leases) < self.limit:
                    for offset in range(len(self.nodes)):
                        index = (self.cursor + offset) % len(self.nodes)
                        node = self.nodes[index]
                        if self.slots[node] > 0:
                            self.cursor = (index + 1) % len(self.nodes)
                            token = uuid.uuid4().hex
                            self.slots[node] -= 1
                            self.leases[token] = (node, owner)
                            return token, node
                self.condition.wait(timeout=1)

    def _check(self) -> None:
        for _, owner in self.leases.values():
            try:
                os.kill(owner, 0)
            except ProcessLookupError:
                self.error = (
                    "candidate owner exited while holding a node lease"
                )
        if self.error:
            raise RuntimeError(self.error)

    def check(self) -> None:
        """Raise when the invocation has been quarantined."""
        with self.condition:
            self._check()

    def register_launch(self, name: str) -> None:
        """Track steps even if their owning worker exits."""
        with self.condition:
            self._check()
            self.launches.add(name)

    def finish_launch(self, name: str) -> None:
        """Forget only steps whose launchers have finished normally."""
        with self.condition:
            self.launches.discard(name)

    def active(self) -> tuple[int, list[str]]:
        """Return outstanding leases and step names for session teardown."""
        with self.condition:
            return len(self.leases), sorted(self.launches)

    def release(self, token: str) -> None:
        """Release a lease after its Slurm work has terminated."""
        with self.condition:
            node, _ = self.leases.pop(token)
            self.slots[node] += 1
            self.condition.notify_all()

    def abort(self, reason: str) -> None:
        """Quarantine all scheduling after uncertain remote termination."""
        with self.condition:
            self.error = reason
            self.condition.notify_all()


_allocator: Allocator | None = None


def _initialize(slots: dict[str, int], limit: int) -> None:
    global _allocator
    _allocator = Allocator(slots, limit)


def _get_allocator() -> Allocator:
    assert _allocator is not None
    return _allocator


class PlacementManager(BaseManager):
    """Local IPC service shared by pygmo workers and candidate threads."""


PlacementManager.register("allocator", callable=_get_allocator)


@dataclass(frozen=True)
class PlacementConnection:
    """Pickle-safe credentials, kept out of configuration and checkpoints."""

    address: tuple[str, int]
    authkey: bytes
    invocation: str

    def connect(self) -> Any:
        """Connect explicitly so spawned workers need no inherited authkey."""
        manager = PlacementManager(address=self.address, authkey=self.authkey)
        manager.connect()
        return manager.allocator()  # type: ignore[attr-defined]


@contextmanager
def placement_session(config: FlowOptConfig) -> Iterator[PlacementConnection]:
    """Own the coordinator for the complete run/optimization invocation."""
    slots = allocation_slots(config)
    authkey = secrets.token_bytes(32)
    manager = PlacementManager(
        address=("127.0.0.1", 0),
        authkey=authkey,
        ctx=multiprocessing.get_context("spawn"),
    )
    manager.start(
        initializer=_initialize,
        initargs=(slots, config.resources.concurrent_evaluations),
    )
    try:
        address = manager.address
        assert isinstance(address, tuple)
        yield PlacementConnection(address, authkey, uuid.uuid4().hex)
    finally:
        manager.shutdown()

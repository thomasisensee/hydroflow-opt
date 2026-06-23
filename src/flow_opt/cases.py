"""Case discovery and the subprocess-worker case contract."""

import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

from flow_opt.models import ParameterSpace


class CasePlugin(Protocol):
    """A plugin that describes a case without loading its heavy runtime."""

    def parameter_space(self, options: dict[str, Any]) -> ParameterSpace:
        """Return the named numerical space optimized by pygmo."""

    def worker_command(
        self,
        request_path: Path,
        result_path: Path,
    ) -> list[str]:
        """Return the isolated worker command for one evaluation."""


class QuadraticCase:
    """Portable built-in case used by examples and tests."""

    def parameter_space(self, options: dict[str, Any]) -> ParameterSpace:
        names = tuple(
            str(name) for name in options.get("parameters", ("x", "y"))
        )
        return ParameterSpace(
            names=names,
            lower_bounds=tuple(-5.0 for _ in names),
            upper_bounds=tuple(5.0 for _ in names),
        )

    def worker_command(
        self,
        request_path: Path,
        result_path: Path,
    ) -> list[str]:
        return [
            sys.executable,
            "-m",
            "flow_opt.toy_worker",
            str(request_path),
            str(result_path),
        ]


def case_from_name(name: str) -> CasePlugin:
    """Load a built-in case or a case registered by an installed package."""

    if name == "quadratic":
        return QuadraticCase()

    entries = metadata.entry_points(group="flow_opt.cases")
    for entry in entries:
        if entry.name != name:
            continue
        loaded = entry.load()
        if isinstance(loaded, type):
            plugin = loaded()
        elif callable(loaded) and not hasattr(loaded, "parameter_space"):
            plugin = loaded()
        else:
            plugin = loaded
        if not hasattr(plugin, "parameter_space") or not hasattr(
            plugin, "worker_command"
        ):
            raise TypeError(
                f"case plugin '{name}' does not implement the case contract"
            )
        return plugin
    raise ValueError(f"unknown case: {name}")

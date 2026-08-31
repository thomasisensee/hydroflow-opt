"""Case discovery and the staged-evaluation case contract."""

import sys
from importlib import metadata
from typing import Any, Protocol, cast

from hydroflow_opt.models import (
    Candidate,
    EvaluationPaths,
    EvaluationPlan,
    EvaluationStage,
    ParameterSpace,
    ResourceRequest,
)


class CasePlugin(Protocol):
    """A plugin that describes a case without loading its heavy runtime."""

    def parameter_space(self, options: dict[str, Any]) -> ParameterSpace:
        """Return the named numerical space optimized by pygmo."""

    def evaluation_plan(
        self,
        candidate: Candidate,
        paths: EvaluationPaths,
        resources: ResourceRequest,
    ) -> EvaluationPlan:
        """Return ordered, backend-neutral commands for one evaluation."""


class QuadraticCase:
    """Portable built-in case used by examples and tests."""

    def parameter_space(self, options: dict[str, Any]) -> ParameterSpace:
        """Return configurable names with fixed quadratic bounds."""
        names = tuple(
            str(name) for name in options.get("parameters", ("x", "y"))
        )
        return ParameterSpace(
            names=names,
            lower_bounds=tuple(-5.0 for _ in names),
            upper_bounds=tuple(5.0 for _ in names),
        )

    def evaluation_plan(
        self,
        candidate: Candidate,
        paths: EvaluationPaths,
        resources: ResourceRequest,
    ) -> EvaluationPlan:
        """Return the single-stage quadratic evaluation plan."""
        del candidate, resources
        return EvaluationPlan(
            stages=(
                EvaluationStage(
                    name="evaluate",
                    command=(
                        sys.executable,
                        "-m",
                        "hydroflow_opt.toy_worker",
                        str(paths.request_path),
                        str(paths.result_path),
                    ),
                    working_directory=paths.evaluation_dir,
                ),
            )
        )


def case_from_name(name: str) -> CasePlugin:
    """Load a built-in case or a case registered by an installed package."""
    if name == "quadratic":
        return QuadraticCase()

    entries = metadata.entry_points(group="hydroflow_opt.cases")
    for entry in entries:
        if entry.name != name:
            continue
        loaded = entry.load()
        if isinstance(loaded, type) or (
            callable(loaded) and not hasattr(loaded, "parameter_space")
        ):
            plugin = loaded()
        else:
            plugin = loaded
        if not hasattr(plugin, "parameter_space") or not hasattr(
            plugin, "evaluation_plan"
        ):
            raise TypeError(
                f"case plugin '{name}' does not implement the case contract"
            )
        return cast(CasePlugin, plugin)
    raise ValueError(f"unknown case: {name}")

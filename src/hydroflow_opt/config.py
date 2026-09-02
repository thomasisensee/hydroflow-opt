"""TOML configuration for case runs and pygmo island optimization."""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from string import Template
from typing import Any

from hydroflow_opt.models import BackendKind, Candidate, ResourceRequest


@dataclass(frozen=True)
class ExecutionConfig:
    """Selection of the mechanism used to launch case workers."""

    backend: BackendKind = BackendKind.LOCAL


@dataclass(frozen=True)
class OptimizationConfig:
    """Settings for the standard differential-evolution island model."""

    islands: int
    population_size: int
    generations: int
    differential_weight: float = 0.8
    crossover_rate: float = 0.9
    topology: str = "fully_connected"
    seed: int | None = None
    initial_population_file: str | None = None
    migrant_handling: str = "preserve"

    def __post_init__(self) -> None:
        """Validate differential-evolution settings."""
        if (
            self.islands < 1
            or self.population_size < 1
            or self.generations < 1
        ):
            raise ValueError("optimization counts must be at least one")
        if self.population_size < 5:
            raise ValueError(
                "optimization.population_size must be at least five for DE"
            )
        if not 0.0 <= self.differential_weight <= 2.0:
            raise ValueError(
                "optimization.differential_weight must be in [0, 2]"
            )
        if not 0.0 <= self.crossover_rate <= 1.0:
            raise ValueError("optimization.crossover_rate must be in [0, 1]")
        if self.topology != "fully_connected":
            raise ValueError(
                "only the 'fully_connected' topology is supported"
            )
        if self.migrant_handling not in {"preserve", "evict"}:
            raise ValueError(
                "optimization.migrant_handling must be 'preserve' or 'evict'"
            )
        if self.seed is not None and not 0 <= self.seed <= 0xFFFFFFFF:
            raise ValueError(
                "optimization.seed must be an unsigned 32-bit integer"
            )


@dataclass(frozen=True)
class FlowOptConfig:
    """Parsed configuration shared by explicit and optimized runs."""

    run_dir: Path
    scratch_dir: Path
    case_name: str
    case_options: dict[str, Any] = field(default_factory=dict)
    candidates: tuple[Candidate, ...] = ()
    resources: ResourceRequest = field(default_factory=ResourceRequest)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    optimization: OptimizationConfig | None = None
    scratch_dir_template: str | None = field(default=None, repr=False)
    scratch_dir_base: Path | None = field(default=None, repr=False)


def load_config(
    path: str | Path, *, resolve_scratch: bool = True
) -> FlowOptConfig:
    """Load and validate a ``hydroflow-opt`` TOML configuration.

    Set ``resolve_scratch`` to false only for validation that runs outside the
    eventual execution environment, such as the ``check`` command.
    """
    config_path = Path(path).resolve()
    with config_path.open("rb") as stream:
        raw = tomllib.load(stream)

    base_dir = config_path.parent
    run = _expect_table(raw, "run")
    case = _expect_table(raw, "case")
    resources = _expect_optional_table(raw, "resources")
    execution = _expect_optional_table(raw, "execution")
    options = case.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("'case.options' must be a TOML table")

    run_dir = _resolve_path(base_dir, _expect_str(run, "directory"))
    scratch_value = run.get("scratch_directory", str(run_dir / "scratch"))
    if not isinstance(scratch_value, str) or not scratch_value:
        raise ValueError("'run.scratch_directory' must be a non-empty string")
    _validate_scratch_template(scratch_value)
    scratch_dir = (
        _resolve_scratch_template(scratch_value, base_dir)
        if resolve_scratch
        else _resolve_path(base_dir, scratch_value)
    )
    return FlowOptConfig(
        run_dir=run_dir,
        scratch_dir=scratch_dir,
        case_name=_expect_str(case, "name"),
        case_options=dict(options),
        candidates=tuple(_parse_candidates(raw.get("candidate", []))),
        resources=ResourceRequest(
            available_cpus=_expect_positive_int(
                resources, "available_cpus", 1
            ),
            concurrent_evaluations=_expect_positive_int(
                resources, "concurrent_evaluations", 1
            ),
            mpi_ranks=_expect_positive_int(resources, "mpi_ranks", 1),
            threads_per_rank=_expect_positive_int(
                resources, "threads_per_rank", 1
            ),
        ),
        execution=_parse_execution(execution),
        optimization=_parse_optimization(raw.get("optimization"), base_dir),
        scratch_dir_template=scratch_value,
        scratch_dir_base=base_dir,
    )


def resolve_runtime_config(
    config: FlowOptConfig,
    environment: Mapping[str, str] | None = None,
) -> FlowOptConfig:
    """Resolve allocation-specific scratch variables for one invocation."""
    if config.scratch_dir_template is None:
        return config
    base_dir = config.scratch_dir_base or Path.cwd()
    scratch_dir = _resolve_scratch_template(
        config.scratch_dir_template,
        base_dir,
        environment,
    )
    return replace(config, scratch_dir=scratch_dir)


def scratch_directory_variables(config: FlowOptConfig) -> tuple[str, ...]:
    """Return environment variables referenced by the scratch template."""
    if config.scratch_dir_template is None:
        return ()
    return tuple(Template(config.scratch_dir_template).get_identifiers())


def _parse_execution(raw: dict[str, Any]) -> ExecutionConfig:
    value = raw.get("backend", BackendKind.LOCAL.value)
    if not isinstance(value, str):
        raise ValueError("'execution.backend' must be a string")
    try:
        backend = BackendKind(value)
    except ValueError as exc:
        choices = ", ".join(item.value for item in BackendKind)
        raise ValueError(
            f"'execution.backend' must be one of: {choices}"
        ) from exc
    return ExecutionConfig(backend=backend)


def _parse_optimization(raw: Any, base_dir: Path) -> OptimizationConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("'optimization' must be a TOML table")
    return OptimizationConfig(
        islands=_expect_positive_int(raw, "islands", 1),
        population_size=_expect_positive_int(raw, "population_size", 8),
        generations=_expect_positive_int(raw, "generations", 1),
        differential_weight=float(raw.get("differential_weight", 0.8)),
        crossover_rate=float(raw.get("crossover_rate", 0.9)),
        topology=str(raw.get("topology", "fully_connected")),
        seed=_expect_optional_seed(raw),
        initial_population_file=_expect_optional_population_path(
            raw, base_dir
        ),
        migrant_handling=str(raw.get("migrant_handling", "preserve")),
    )


def _expect_optional_seed(raw: dict[str, Any]) -> int | None:
    value = raw.get("seed")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("'optimization.seed' must be an integer")
    return value


def _expect_optional_population_path(
    raw: dict[str, Any], base_dir: Path
) -> str | None:
    value = raw.get("initial_population_file")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(
            "'optimization.initial_population_file' must be a non-empty string"
        )
    return str(_resolve_path(base_dir, value).resolve())


def _parse_candidates(raw: Any) -> list[Candidate]:
    if not isinstance(raw, list):
        raise ValueError("'candidate' must be an array of TOML tables")
    candidates: list[Candidate] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError("each candidate must be a TOML table")
        parameters = item.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("'candidate.parameters' must be a table")
        candidates.append(
            Candidate(
                id=str(item.get("id", f"candidate-{index}")),
                parameters={
                    str(key): float(value) for key, value in parameters.items()
                },
            )
        )
    return candidates


def _expect_table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"'{name}' must be a TOML table")
    return value


def _expect_optional_table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"'{name}' must be a TOML table")
    return value


def _expect_str(raw: dict[str, Any], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{name}' must be a non-empty string")
    return value


def _expect_positive_int(raw: dict[str, Any], name: str, default: int) -> int:
    value = int(raw.get(name, default))
    if value < 1:
        raise ValueError(f"'{name}' must be at least one")
    return value


def _resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _validate_scratch_template(value: str) -> None:
    if not Template(value).is_valid():
        raise ValueError(
            "'run.scratch_directory' contains an invalid environment "
            "variable placeholder"
        )


def _resolve_scratch_template(
    value: str,
    base_dir: Path,
    environment: Mapping[str, str] | None = None,
) -> Path:
    template = Template(value)
    _validate_scratch_template(value)
    variables = template.get_identifiers()
    values = os.environ if environment is None else environment
    for name in variables:
        if not values.get(name):
            raise ValueError(
                f"run.scratch_directory requires non-empty environment "
                f"variable '{name}'"
            )
    expanded = template.substitute(values)
    return _resolve_path(base_dir, expanded)

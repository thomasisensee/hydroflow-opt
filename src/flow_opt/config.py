"""TOML configuration for case runs and pygmo island optimization."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flow_opt.models import Candidate, ResourceRequest


@dataclass(frozen=True)
class OptimizationConfig:
    """Settings for the standard differential-evolution island model."""

    islands: int
    population_size: int
    generations: int
    differential_weight: float = 0.8
    crossover_rate: float = 0.9
    topology: str = "fully_connected"

    def __post_init__(self) -> None:
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


@dataclass(frozen=True)
class FlowOptConfig:
    """Parsed configuration shared by explicit and optimized runs."""

    run_dir: Path
    scratch_dir: Path
    case_name: str
    case_options: dict[str, Any] = field(default_factory=dict)
    candidates: tuple[Candidate, ...] = ()
    resources: ResourceRequest = field(default_factory=ResourceRequest)
    optimization: OptimizationConfig | None = None


def load_config(path: str | Path) -> FlowOptConfig:
    """Load and validate a ``flow-opt`` TOML configuration."""

    config_path = Path(path).resolve()
    with config_path.open("rb") as stream:
        raw = tomllib.load(stream)

    base_dir = config_path.parent
    run = _expect_table(raw, "run")
    case = _expect_table(raw, "case")
    resources = _expect_optional_table(raw, "resources")
    options = case.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("'case.options' must be a TOML table")

    run_dir = _resolve_path(base_dir, _expect_str(run, "directory"))
    scratch_dir = _resolve_path(
        base_dir, run.get("scratch_directory", run_dir / "scratch")
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
        optimization=_parse_optimization(raw.get("optimization")),
    )


def _parse_optimization(raw: Any) -> OptimizationConfig | None:
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
    )


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

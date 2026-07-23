"""Local execution, resumable optimization, and run inspection."""

import hashlib
import json
import os
import platform
import secrets
import shutil
import uuid
import warnings
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from hydroflow_opt.backends import (
    SubprocessBackend as SubprocessBackend,
)
from hydroflow_opt.backends import (
    backend_from_config,
    validate_execution_environment,
)
from hydroflow_opt.backends.worker import (
    archive_attempt,
    result_from_json,
    result_to_json,
    write_result,
)
from hydroflow_opt.cases import CasePlugin, case_from_name
from hydroflow_opt.config import (
    ExecutionConfig,
    FlowOptConfig,
    OptimizationConfig,
)
from hydroflow_opt.models import (
    BackendKind,
    Candidate,
    EvaluationBackend,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    ResourceRequest,
)

_MANIFEST_SCHEMA = 2
_SUPPORTED_MANIFEST_SCHEMAS = {1, _MANIFEST_SCHEMA}
_CHECKPOINT_SCHEMA = 1
_PENALTY = 1.0e12


@dataclass(frozen=True)
class RunSummary:
    """Aggregate information about a completed run."""

    total: int
    succeeded: int
    failed: int
    results_path: Path
    summary_path: Path


def run_local(
    config: FlowOptConfig,
    *,
    config_path: Path | None = None,
    backend: EvaluationBackend | None = None,
) -> RunSummary:
    """Evaluate explicit candidates with the configured isolated worker."""

    if not config.candidates:
        raise ValueError("'run' requires at least one [[candidate]] entry")
    case = case_from_name(config.case_name)
    if backend is None:
        validate_execution_environment(config)
    evaluator = backend or backend_from_config(config, case)
    _prepare_run(config, config_path)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(
        max_workers=config.resources.concurrent_evaluations
    ) as executor:
        results = list(executor.map(evaluator.evaluate, config.candidates))
    return _write_summary(config, results)


def run_optimization(
    config: FlowOptConfig,
    *,
    config_path: Path | None = None,
    backend: EvaluationBackend | None = None,
) -> RunSummary:
    """Start a new resumable pygmo DE optimization."""

    _validate_optimization(config)
    if backend is None:
        validate_execution_environment(config)
    pg = _import_pygmo()
    _prepare_new_optimization_run(config, config_path)
    optimization = config.optimization
    assert optimization is not None
    seed = (
        optimization.seed
        if optimization.seed is not None
        else secrets.randbits(32)
    )
    resolved = replace(
        config,
        optimization=replace(optimization, seed=seed),
    )
    case = case_from_name(resolved.case_name)
    space = case.parameter_space(resolved.case_options)
    manifest = _new_manifest(
        resolved, case, space, _backend_name(resolved, backend)
    )
    _atomic_json(_manifest_path(resolved.run_dir), manifest)
    return _continue_optimization(resolved, manifest, pg, backend)


def resume_optimization(
    run_dir: str | Path,
    *,
    backend: EvaluationBackend | None = None,
) -> RunSummary:
    """Continue a compatible optimization from its latest checkpoint."""

    run_path = Path(run_dir).resolve()
    manifest = _read_json(_manifest_path(run_path))
    if manifest.get("schema_version") not in _SUPPORTED_MANIFEST_SCHEMAS:
        raise ValueError("unsupported optimization manifest schema")
    if manifest.get("kind") != "optimization":
        raise ValueError("run is not a resumable optimization")
    if manifest.get("status") == "complete":
        return inspect_run(run_path)
    if _json_hash(manifest["config"]) != manifest.get("config_hash"):
        raise ValueError("effective run configuration does not match its hash")

    config = _config_from_manifest(run_path, manifest["config"])
    _validate_optimization(config)
    if backend is None:
        validate_execution_environment(config)
    pg = _import_pygmo()
    case = case_from_name(config.case_name)
    space = case.parameter_space(config.case_options)
    _validate_parameter_space(manifest["parameter_space"], space)

    provenance = _provenance(case, _backend_name(config, backend))
    compatibility_warnings = _provenance_warnings(
        manifest["provenance"][-1], provenance
    )
    for message in compatibility_warnings:
        warnings.warn(message, RuntimeWarning, stacklevel=2)
    provenance["resumed_at"] = _now()
    provenance["warnings"] = compatibility_warnings
    manifest["provenance"].append(provenance)
    manifest["status"] = "running"
    checkpoint = _load_checkpoint(config.run_dir, required=False)
    if checkpoint is not None:
        manifest["evaluation_ids"] = checkpoint["evaluation_ids"]
        _sync_history(config.run_dir, checkpoint.get("history", []))
    _atomic_json(_manifest_path(config.run_dir), manifest)
    return _continue_optimization(config, manifest, pg, backend)


class _OptimizationProblem:
    """Pickle-safe pygmo UDP delegating fitness calls to a backend."""

    def __init__(
        self,
        config: FlowOptConfig,
        run_id: str,
        island: int,
        generation: int,
        phase: str,
        backend: EvaluationBackend | None = None,
    ) -> None:
        self.config = config
        self.run_id = run_id
        self.island = island
        self.generation = generation
        self.phase = phase
        self.backend = backend
        self.position = 0
        self.space = case_from_name(config.case_name).parameter_space(
            config.case_options
        )

    def fitness(self, vector: list[float]) -> list[float]:
        position = self.position
        self.position += 1
        if self.phase == "initial":
            candidate_id = f"island-{self.island:03d}-initial-{position:03d}"
        else:
            candidate_id = (
                f"island-{self.island:03d}-generation-"
                f"{self.generation:06d}-trial-{position:03d}"
            )
        context = EvaluationContext(
            run_id=self.run_id,
            island=self.island,
            generation=self.generation,
            phase=self.phase,
            position=position,
        )
        candidate = Candidate(
            id=candidate_id,
            parameters=self.space.decode(tuple(vector)),
        )
        evaluator = self.backend or backend_from_config(
            self.config, case_from_name(self.config.case_name)
        )
        result = _evaluate_optimization_candidate(
            self.config, evaluator, candidate, context
        )
        return [result.objective if result.objective is not None else _PENALTY]

    def get_bounds(self) -> tuple[tuple[float, ...], tuple[float, ...]]:
        return self.space.lower_bounds, self.space.upper_bounds


def inspect_run(run_dir: str | Path) -> RunSummary:
    """Read a completed run summary from disk."""

    summary_path = Path(run_dir) / "summary.json"
    raw = json.loads(summary_path.read_text(encoding="utf-8"))
    return RunSummary(
        total=int(raw["total"]),
        succeeded=int(raw["succeeded"]),
        failed=int(raw["failed"]),
        results_path=Path(raw["results_path"]),
        summary_path=Path(raw["summary_path"]),
    )


def _continue_optimization(
    config: FlowOptConfig,
    manifest: dict[str, Any],
    pg: Any,
    backend: EvaluationBackend | None,
) -> RunSummary:
    optimization = config.optimization
    assert optimization is not None and optimization.seed is not None
    checkpoint = _load_checkpoint(config.run_dir, required=False)
    if checkpoint is None:
        checkpoint = {
            "schema_version": _CHECKPOINT_SCHEMA,
            "run_id": manifest["run_id"],
            "islands": [],
            "migrants_db": [],
            "evaluation_ids": [],
            "history": [],
        }
    _validate_checkpoint(checkpoint, manifest, optimization)
    _initialize_populations(config, manifest, checkpoint, pg, backend)

    completed_generation = min(
        int(item["generation"]) for item in checkpoint["islands"]
    )
    for generation in range(
        completed_generation + 1, optimization.generations + 1
    ):
        archipelago = _build_archipelago(
            config, manifest, checkpoint, generation, pg, backend
        )
        archipelago.evolve()
        archipelago.wait_check()
        checkpoint["islands"] = [
            _population_state(
                archipelago[index].get_population(), index, generation
            )
            for index in range(optimization.islands)
        ]
        checkpoint["migrants_db"] = _serialize_migrants(
            archipelago.get_migrants_db()
        )
        checkpoint["evaluation_ids"].extend(
            _generation_ids(optimization, generation)
        )
        checkpoint["history"].append(
            {
                "generation": generation,
                "champions": [
                    item["champion"] for item in checkpoint["islands"]
                ],
            }
        )
        _save_checkpoint(config.run_dir, checkpoint)
        _sync_history(config.run_dir, checkpoint["history"])
        manifest["evaluation_ids"] = checkpoint["evaluation_ids"]
        manifest["status"] = "running"
        _atomic_json(_manifest_path(config.run_dir), manifest)

    _write_final_state(config.run_dir, checkpoint)
    results = _read_owned_results(config, checkpoint["evaluation_ids"])
    summary = _write_summary(config, results)
    manifest["evaluation_ids"] = checkpoint["evaluation_ids"]
    manifest["status"] = "complete"
    manifest["completed_at"] = _now()
    _atomic_json(_manifest_path(config.run_dir), manifest)
    return summary


def _initialize_populations(
    config: FlowOptConfig,
    manifest: dict[str, Any],
    checkpoint: dict[str, Any],
    pg: Any,
    backend: EvaluationBackend | None,
) -> None:
    optimization = config.optimization
    assert optimization is not None and optimization.seed is not None
    for island in range(len(checkpoint["islands"]), optimization.islands):
        problem = pg.problem(
            _OptimizationProblem(
                config,
                manifest["run_id"],
                island,
                0,
                "initial",
                backend,
            )
        )
        population = pg.population(
            problem,
            optimization.population_size,
            seed=_derived_seed(optimization.seed, "population", island, 0),
        )
        checkpoint["islands"].append(_population_state(population, island, 0))
        checkpoint["evaluation_ids"].extend(
            _initial_ids(island, optimization.population_size)
        )
        _save_checkpoint(config.run_dir, checkpoint)
        manifest["evaluation_ids"] = checkpoint["evaluation_ids"]
        _atomic_json(_manifest_path(config.run_dir), manifest)


def _build_archipelago(
    config: FlowOptConfig,
    manifest: dict[str, Any],
    checkpoint: dict[str, Any],
    generation: int,
    pg: Any,
    backend: EvaluationBackend | None,
) -> Any:
    optimization = config.optimization
    assert optimization is not None and optimization.seed is not None
    archipelago = pg.archipelago(t=pg.fully_connected())
    for island, state in enumerate(checkpoint["islands"]):
        problem = pg.problem(
            _OptimizationProblem(
                config,
                manifest["run_id"],
                island,
                generation,
                "trial",
                backend,
            )
        )
        population = pg.population(problem)
        for vector, fitness in zip(state["x"], state["f"], strict=True):
            population.push_back(vector, fitness)
        algorithm = pg.algorithm(
            pg.de(
                gen=1,
                F=optimization.differential_weight,
                CR=optimization.crossover_rate,
                seed=_derived_seed(
                    optimization.seed, "de", island, generation
                ),
            )
        )
        archipelago.push_back(
            pg.island(udi=pg.mp_island(), algo=algorithm, pop=population)
        )
    if checkpoint["migrants_db"]:
        archipelago.set_migrants_db(
            _deserialize_migrants(checkpoint["migrants_db"])
        )
    return archipelago


def _population_state(population: Any, island: int, generation: int) -> dict:
    return {
        "island": island,
        "generation": generation,
        "x": population.get_x().tolist(),
        "f": population.get_f().tolist(),
        "champion": {
            "x": population.champion_x.tolist(),
            "f": population.champion_f.tolist(),
        },
    }


def _serialize_migrants(database: list[Any]) -> list[dict[str, Any]]:
    return [
        {"ids": ids.tolist(), "x": x.tolist(), "f": f.tolist()}
        for ids, x, f in database
    ]


def _deserialize_migrants(database: list[dict[str, Any]]) -> list[Any]:
    import numpy as np

    return [
        (
            np.asarray(item["ids"], dtype=np.uint64),
            np.asarray(item["x"], dtype=float),
            np.asarray(item["f"], dtype=float),
        )
        for item in database
    ]


def _write_final_state(run_dir: Path, checkpoint: dict[str, Any]) -> None:
    optimization_dir = run_dir / "optimization"
    populations = {
        "schema_version": 1,
        "populations": checkpoint["islands"],
    }
    champions = [item["champion"] for item in checkpoint["islands"]]
    overall = min(champions, key=lambda item: item["f"][0])
    _atomic_json(optimization_dir / "final-populations.json", populations)
    _atomic_json(
        optimization_dir / "champions.json",
        {"schema_version": 1, "islands": champions, "overall": overall},
    )


def _validate_optimization(config: FlowOptConfig) -> None:
    if config.optimization is None:
        raise ValueError("'optimize' requires an [optimization] table")
    if config.optimization.islands > config.resources.concurrent_evaluations:
        raise ValueError(
            "optimization.islands must not exceed "
            "resources.concurrent_evaluations"
        )


def _prepare_new_optimization_run(
    config: FlowOptConfig, config_path: Path | None
) -> None:
    if config.run_dir.exists() and any(config.run_dir.iterdir()):
        raise ValueError(
            f"optimization run directory is not empty: {config.run_dir}"
        )
    _prepare_run(config, config_path)
    (config.run_dir / "optimization").mkdir(parents=True, exist_ok=True)


def _prepare_run(config: FlowOptConfig, config_path: Path | None) -> None:
    config.run_dir.mkdir(parents=True, exist_ok=True)
    config.scratch_dir.mkdir(parents=True, exist_ok=True)
    if config_path is not None:
        shutil.copy2(config_path, config.run_dir / "config.toml")


def _new_manifest(
    config: FlowOptConfig,
    case: CasePlugin,
    space: Any,
    backend_name: str,
) -> dict[str, Any]:
    effective = _effective_config(config)
    provenance = _provenance(case, backend_name)
    provenance["started_at"] = _now()
    return {
        "schema_version": _MANIFEST_SCHEMA,
        "kind": "optimization",
        "run_id": uuid.uuid4().hex,
        "status": "running",
        "created_at": _now(),
        "config": effective,
        "config_hash": _json_hash(effective),
        "parameter_space": {
            "names": list(space.names),
            "lower_bounds": list(space.lower_bounds),
            "upper_bounds": list(space.upper_bounds),
        },
        "reproducibility": "best_effort",
        "provenance": [provenance],
        "evaluation_ids": [],
    }


def _effective_config(config: FlowOptConfig) -> dict[str, Any]:
    assert config.optimization is not None
    return {
        "run_dir": str(config.run_dir.resolve()),
        "scratch_dir": str(config.scratch_dir.resolve()),
        "case_name": config.case_name,
        "case_options": config.case_options,
        "resources": asdict(config.resources),
        "execution": {"backend": config.execution.backend.value},
        "optimization": asdict(config.optimization),
    }


def _config_from_manifest(run_dir: Path, raw: dict[str, Any]) -> FlowOptConfig:
    return FlowOptConfig(
        run_dir=run_dir,
        scratch_dir=Path(raw["scratch_dir"]),
        case_name=str(raw["case_name"]),
        case_options=dict(raw["case_options"]),
        resources=ResourceRequest(**raw["resources"]),
        execution=ExecutionConfig(
            backend=BackendKind(
                raw.get("execution", {}).get("backend", "local")
            )
        ),
        optimization=OptimizationConfig(**raw["optimization"]),
    )


def _provenance(case: CasePlugin, backend_name: str) -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "execution_backend": backend_name,
        "packages": {
            "hydroflow_opt": _package_version("hydroflow_opt"),
            "pygmo": _package_version("pygmo"),
            "numpy": _package_version("numpy"),
        },
        "case_plugin": _plugin_version(case),
    }


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "unknown"


def _plugin_version(case: CasePlugin) -> dict[str, str]:
    module = type(case).__module__.split(".")[0]
    distributions = metadata.packages_distributions().get(module, [])
    distribution = distributions[0] if distributions else module
    return {
        "module": module,
        "distribution": distribution,
        "version": _package_version(distribution),
    }


def _provenance_warnings(
    previous: dict[str, Any], current: dict[str, Any]
) -> list[str]:
    messages: list[str] = []
    for label in (
        "python",
        "platform",
        "execution_backend",
        "packages",
        "case_plugin",
    ):
        if previous.get(label) != current.get(label):
            messages.append(
                f"resume environment differs for {label}: "
                f"{previous.get(label)!r} -> {current.get(label)!r}"
            )
    return messages


def _backend_name(
    config: FlowOptConfig, backend: EvaluationBackend | None
) -> str:
    if backend is None:
        return config.execution.backend.value
    backend_type = type(backend)
    return f"{backend_type.__module__}.{backend_type.__qualname__}"


def _validate_parameter_space(raw: dict[str, Any], space: Any) -> None:
    current = {
        "names": list(space.names),
        "lower_bounds": list(space.lower_bounds),
        "upper_bounds": list(space.upper_bounds),
    }
    if raw != current:
        raise ValueError(
            "case parameter names or bounds are incompatible with checkpoint"
        )


def _validate_checkpoint(
    checkpoint: dict[str, Any],
    manifest: dict[str, Any],
    optimization: OptimizationConfig,
) -> None:
    if checkpoint.get("schema_version") != _CHECKPOINT_SCHEMA:
        raise ValueError("unsupported optimization checkpoint schema")
    if checkpoint.get("run_id") != manifest.get("run_id"):
        raise ValueError("checkpoint does not belong to this optimization run")
    if len(checkpoint.get("islands", [])) > optimization.islands:
        raise ValueError("checkpoint contains too many islands")
    generations = {
        int(state.get("generation", -1))
        for state in checkpoint.get("islands", [])
    }
    if len(generations) > 1:
        raise ValueError("checkpoint islands are at different generations")
    dimension = len(manifest["parameter_space"]["names"])
    for state in checkpoint.get("islands", []):
        if len(state.get("x", [])) != optimization.population_size:
            raise ValueError("checkpoint population size is incompatible")
        if len(state.get("x", [])) != len(state.get("f", [])):
            raise ValueError(
                "checkpoint population vectors and fitness differ"
            )
        if any(len(vector) != dimension for vector in state.get("x", [])):
            raise ValueError(
                "checkpoint decision-vector dimension is incompatible"
            )
        if any(len(fitness) != 1 for fitness in state.get("f", [])):
            raise ValueError("checkpoint fitness dimension is incompatible")


def _derived_seed(
    base: int, purpose: str, island: int, generation: int
) -> int:
    value = f"{base}:{purpose}:{island}:{generation}".encode()
    return int.from_bytes(hashlib.sha256(value).digest()[:4], "big")


def _initial_ids(island: int, population_size: int) -> list[str]:
    return [
        f"island-{island:03d}-initial-{position:03d}"
        for position in range(population_size)
    ]


def _generation_ids(
    optimization: OptimizationConfig, generation: int
) -> list[str]:
    return [
        f"island-{island:03d}-generation-{generation:06d}-trial-{position:03d}"
        for island in range(optimization.islands)
        for position in range(optimization.population_size)
    ]


def _save_checkpoint(run_dir: Path, checkpoint: dict[str, Any]) -> None:
    _atomic_json(run_dir / "optimization" / "checkpoint.json", checkpoint)


def _load_checkpoint(
    run_dir: Path, *, required: bool
) -> dict[str, Any] | None:
    path = run_dir / "optimization" / "checkpoint.json"
    if not path.exists() and not required:
        return None
    return _read_json(path)


def _sync_history(run_dir: Path, history: list[dict[str, Any]]) -> None:
    path = run_dir / "optimization" / "history.jsonl"
    content = "".join(json.dumps(item) + "\n" for item in history)
    _atomic_text(path, content)


def _manifest_path(run_dir: Path) -> Path:
    return run_dir / "manifest.json"


def _read_owned_results(
    config: FlowOptConfig, evaluation_ids: list[str]
) -> list[EvaluationResult]:
    results: list[EvaluationResult] = []
    for candidate_id in evaluation_ids:
        evaluation_dir = config.run_dir / "evaluations" / candidate_id
        raw = _read_json(evaluation_dir / "outcome.json")
        results.append(result_from_json(raw, candidate_id, evaluation_dir))
    return results


def _evaluate_optimization_candidate(
    config: FlowOptConfig,
    backend: EvaluationBackend,
    candidate: Candidate,
    context: EvaluationContext,
) -> EvaluationResult:
    evaluation_dir = config.run_dir / "evaluations" / candidate.id
    evaluation_dir.mkdir(parents=True, exist_ok=True)
    identity_path = evaluation_dir / "evaluation.json"
    outcome_path = evaluation_dir / "outcome.json"
    identity = {
        "candidate": asdict(candidate),
        "context": asdict(context),
        "case": {
            "name": config.case_name,
            "options": config.case_options,
        },
    }
    if identity_path.exists():
        try:
            previous = _read_json(identity_path)
        except (OSError, ValueError, json.JSONDecodeError):
            archive_attempt(evaluation_dir)
        else:
            if previous != identity:
                archive_attempt(evaluation_dir)
            elif outcome_path.exists():
                try:
                    return result_from_json(
                        _read_json(outcome_path), candidate.id, evaluation_dir
                    )
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pass
    _atomic_json(identity_path, identity)
    result = backend.evaluate(candidate, context)
    write_result(outcome_path, result)
    return result


def _write_summary(
    config: FlowOptConfig, results: list[EvaluationResult]
) -> RunSummary:
    results_path = config.run_dir / "results.jsonl"
    _atomic_text(
        results_path,
        "".join(
            json.dumps(result_to_json(result)) + "\n" for result in results
        ),
    )
    succeeded = sum(
        result.status is EvaluationStatus.SUCCESS for result in results
    )
    summary = RunSummary(
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results_path=results_path,
        summary_path=config.run_dir / "summary.json",
    )
    _atomic_json(summary.summary_path, asdict(summary))
    return summary


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, default=str, indent=2) + "\n")


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return raw


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _import_pygmo() -> Any:
    try:
        import pygmo as pg  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - required in releases.
        raise RuntimeError(
            "pygmo is required for hydroflow-opt optimization"
        ) from exc
    return pg

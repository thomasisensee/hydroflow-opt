# Writing a case plugin

A plugin is a separate Python package with a case object and one or more runtime commands.

## Start with the smallest plugin

Only two methods are required: `parameter_space(options)` declares the inputs, and `evaluation_plan(candidate, paths, resources)` describes the commands to run. No base class is required. The backend writes the request, executes the plan, captures logs and timings, and records failures. Your runtime computes the objective and writes the result.

A useful package layout is:

```text
pyproject.toml                # Package metadata and case entry point
src/example_opt/__init__.py   # May be empty
src/example_opt/case.py       # Parameter bounds and command plan
src/example_opt/runtime.py    # Simulation and objective calculation
```

Start with a single serial stage if the evaluation fits in one Python process. For example, put this in `case.py`:

```python
import sys
from typing import Any

from hydroflow_opt import (
    Candidate,
    EvaluationPaths,
    EvaluationPlan,
    EvaluationStage,
    ParameterSpace,
    ResourceRequest,
)


class ExampleCase:
    def parameter_space(self, options: dict[str, Any]) -> ParameterSpace:
        return ParameterSpace(
            names=("x", "y"),
            lower_bounds=(-1.0, -1.0),
            upper_bounds=(1.0, 1.0),
        )

    def evaluation_plan(
        self,
        candidate: Candidate,
        paths: EvaluationPaths,
        resources: ResourceRequest,
    ) -> EvaluationPlan:
        return EvaluationPlan(
            stages=(
                EvaluationStage(
                    name="evaluate",
                    command=(
                        sys.executable,
                        "-m",
                        "example_opt.runtime",
                        str(paths.request_path),
                        str(paths.result_path),
                    ),
                    working_directory=paths.scratch_dir,
                ),
            )
        )
```

Pair it with this complete `runtime.py`:

```python
import argparse
import json
from pathlib import Path

from hydroflow_opt import EvaluationResult
from hydroflow_opt.results import write_result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    candidate = request["candidate"]

    # Replace this expression with your simulation and objective calculation.
    # Import solver/geometry libraries here, away from plugin discovery.
    parameters = candidate["parameters"]
    objective = float(parameters["x"]) ** 2 + float(parameters["y"]) ** 2
    write_result(
        args.result,
        EvaluationResult.success(candidate["id"], objective),
    )


if __name__ == "__main__":
    main()
```

This example needs no state file, custom loader, resource copying, or separate stage dispatcher. Register it as described below and install the package into the environment running hydroflow-opt.

The runtime can also read `request["case"]["options"]` for case settings,
`request["context"]["scratch_dir"]` for temporary files, and
`request["context"]["resources"]` for allocated resources. Parameter values
are keyed by the names declared in `ParameterSpace`.

## Register the case

Declare an entry point in the plugin's `pyproject.toml`:

```toml
[project.entry-points."hydroflow_opt.cases"]
example = "example_opt.case:ExampleCase"
```

The entry-point name is used in configuration:

```toml
[case]
name = "example"
```

## Split into stages when needed

For an external MPI solver, use serial preparation and finalization commands around a solver stage with `StageResources`. The backend then supplies the appropriate launcher. Separate stages also provide separate logs and timings. The following is a plan skeleton; implement the referenced `example_opt.stages` commands for your simulation:

```python
import sys

from hydroflow_opt import (
    Candidate,
    EvaluationPaths,
    EvaluationPlan,
    EvaluationStage,
    ParameterSpace,
    ResourceRequest,
    StageResources,
)


class ExampleCase:
    def parameter_space(self, options):
        return ParameterSpace(
            names=("x", "y"),
            lower_bounds=(-1.0, -1.0),
            upper_bounds=(1.0, 1.0),
        )

    def evaluation_plan(
        self,
        candidate: Candidate,
        paths: EvaluationPaths,
        resources: ResourceRequest,
    ) -> EvaluationPlan:
        runtime = (sys.executable, "-m", "example_opt.stages")
        solver_resources = StageResources(
            resources.mpi_ranks,
            resources.threads_per_rank,
        )
        return EvaluationPlan(
            stages=(
                EvaluationStage(
                    name="prepare",
                    command=(*runtime, "prepare", str(paths.request_path)),
                    working_directory=paths.scratch_dir,
                ),
                EvaluationStage(
                    name="solve",
                    command=("example-solver",),
                    working_directory=paths.scratch_dir,
                    resources=solver_resources,
                ),
                EvaluationStage(
                    name="finalize",
                    command=(
                        *runtime,
                        "finalize",
                        str(paths.request_path),
                        str(paths.result_path),
                    ),
                    working_directory=paths.scratch_dir,
                ),
            )
        )
```

Keep this module importable without solver or geometry dependencies. Those dependencies belong in the subprocess modules invoked by the plan.

Each stage starts a new process. Intermediate simulation files can be the handoff; add a plugin-owned JSON state file only for information later stages cannot derive from the request or those files. The core does not interpret that state file. There is no required number of stages or prescribed runtime module layout.

## What to reuse from TiStoS

TiStoS is an adapter for an existing dtOO/OpenFOAM implementation, rather than the minimum template for a new plugin:

| Part | Purpose | Needed by a new plugin? |
| --- | --- | --- |
| `case.py` | Parameter bounds and ordered stage commands | Yes, or equivalent code |
| `stages.py` | Dispatch command-line arguments to Python functions | Only if you use this command layout |
| `prepare`, `configure_turbulent`, `finalize` | Mesh, configure two solves, calculate the objective | Choose stages to suit your simulation |
| OpenFOAM dictionary edits and decomposition | Configure this solver and its parallel case | Only for cases needing those operations |
| Data staging and geometry subprocesses | Isolate mutable inputs and support relative XML paths | Only if your implementation needs it |
| State JSON | Carry preparation history and generated paths across processes | Only if you need that handoff |

For a new implementation, ordinary imports inside runtime functions and explicit paths can avoid much of the legacy adapter. Keep solver-specific operations in the case package. Repeated request parsing, state serialization, and command logging are candidates for future shared runtime utilities; they are not additional interfaces that every plugin must implement.

## Write the result

The final command writes an {py:class}`~hydroflow_opt.EvaluationResult`:

```python
from hydroflow_opt import EvaluationResult
from hydroflow_opt.results import write_result

result = EvaluationResult.success(
    candidate_id,
    objective,
    metadata={"case_value": value},
)
write_result(result_path, result)
```

The core adds measured stage timings and execution metadata afterward. Let an uncaught exception or nonzero external command stop a failed stage; the backend records its stderr and creates the failed result.

## Plugin rules

- Never construct `mpiexec` or `srun`; declare `StageResources` instead.
- Give every candidate isolated mutable files.
- Use `EvaluationPaths` rather than fixed cluster paths.
- Use files, not in-memory state, between stages.
- Keep stage names stable so timings remain comparable.
- Clean generated files only after the final result has been computed.

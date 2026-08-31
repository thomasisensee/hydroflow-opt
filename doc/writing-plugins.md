# Writing a case plugin

A plugin is a separate Python package with a case object and one or more runtime commands.

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

## Implement the contract

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

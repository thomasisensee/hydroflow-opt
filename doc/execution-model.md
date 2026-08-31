# Execution model

## Candidates and plans

A {py:class}`~hydroflow_opt.Candidate` is an identifier plus named numerical parameters. The selected {py:class}`~hydroflow_opt.CasePlugin` converts it into an {py:class}`~hydroflow_opt.EvaluationPlan`.

A plan is an ordered tuple of {py:class}`~hydroflow_opt.EvaluationStage` objects. A stage declares:

- a unique name;
- an argument tuple, executed without a shell;
- a working directory;
- a backend-neutral process and thread shape.

Stages run sequentially within one evaluation. Different evaluations run concurrently up to `resources.concurrent_evaluations`. Plugins pass data between stages through files in the supplied evaluation or scratch paths.

The final stage must write a structured result to `EvaluationPaths.result_path`. The core validates the candidate ID, status, objective, timings, and metadata.

## Resource model

The run-level CPU limit is:

```text
concurrent_evaluations × mpi_ranks × threads_per_rank ≤ available_cpus
```

`mpi_ranks × threads_per_rank` defines one evaluation slot. An individual stage can request fewer resources but not more than that slot. Serial setup or post-processing stages normally use `StageResources()`; solver stages normally use the configured ranks and threads.

The backend sets `OMP_NUM_THREADS` to the stage's thread count.

## Local backend

One-process stages run directly. Stages requesting multiple processes become:

```text
mpiexec -n <processes> <stage command>
```

The plugin must not include `mpiexec` itself.

## Slurm backend

The Slurm backend requires an existing `sbatch` or `salloc` allocation. Every stage becomes an exclusive one-node job step:

```text
srun --exclusive --nodes=1 \
  --ntasks=<processes> --cpus-per-task=<threads> \
  --cpu-bind=cores [--mpi=pmix] <stage command>
```

`--mpi=pmix` is added for multi-process stages. Successive stages may run on different nodes, so their evaluation and scratch paths must be visible from every allocated node. Queue, wall-time, and allocation size remain properties of the outer Slurm job.

## Failures and repeated evaluations

A stage that cannot start or exits nonzero stops the plan and produces a failed result. Invalid or missing final output also becomes a failed result. Completed stage timings and the failed stage name are retained.

If the same request already has a valid result, the backend reuses it. If an evaluation directory contains incompatible or invalid files, those files are moved to `attempts/attempt-NNNN/` before another attempt.

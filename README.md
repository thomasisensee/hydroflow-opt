# hydroflow-opt

[![License](https://img.shields.io/pypi/l/hydroflow-opt?label=License)](https://opensource.org/licenses/MIT)
[![Build](https://github.com/thomasisensee/hydroflow-opt/actions/workflows/ci.yml/badge.svg)](https://github.com/thomasisensee/hydroflow-opt/actions)
[![codecov](https://codecov.io/gh/thomasisensee/hydroflow-opt/graph/badge.svg?token=KTY4LT5GNW)](https://codecov.io/gh/thomasisensee/hydroflow-opt)
[![pre-commit.ci](https://results.pre-commit.ci/badge/github/thomasisensee/hydroflow-opt/main.svg)](https://results.pre-commit.ci/latest/github/thomasisensee/hydroflow-opt/main)
[![PyPI](https://img.shields.io/pypi/v/hydroflow-opt?logo=pypi&logoColor=gold&label=PyPI)](https://pypi.org/project/hydroflow-opt)
[![Python](https://img.shields.io/pypi/pyversions/hydroflow-opt?logo=python&logoColor=gold&label=Python)](https://pypi.org/project/hydroflow-opt)

`hydroflow-opt` is a Linux/Python 3.11–3.13 orchestration package for simulation-based optimization. It uses pygmo's island model and runs individual case evaluations in isolated subprocesses.

Cases are supplied by installed plugins. The package includes a deterministic `quadratic` case for laptop development and tests. A real case, such as `hydrofoil-opt`, depends on `hydroflow-opt` rather than the reverse.

## Installation

Install with uv or pip on a supported Linux system:

```bash
uv sync --extra tests
# or
python -m pip install --editable '.[tests]'
```

`pygmo` is a required dependency. A simulation case may have additional runtime prerequisites, but those must not be imported by `hydroflow-opt` itself.

## Run explicit candidates

```bash
hydroflow-opt check examples/quadratic.toml
hydroflow-opt run examples/quadratic.toml
hydroflow-opt inspect examples/runs/quadratic
```

```toml
[run]
directory = "runs/quadratic"
scratch_directory = "runs/quadratic/scratch"

[case]
name = "quadratic"

[resources]
available_cpus = 1
concurrent_evaluations = 1
mpi_ranks = 1
threads_per_rank = 1

[[candidate]]
id = "baseline"
[candidate.parameters]
x = 1.0
y = 2.0
```

Each candidate gets its own request, result, scratch directory, and ordered stage records. Every stage has separate stdout, stderr, timing, command, and resource metadata. The resource invariant is:

```text
concurrent_evaluations × mpi_ranks × threads_per_rank ≤ available_cpus
```

`hydroflow-opt` refuses a configuration that violates it. It also rejects a stage whose CPU request exceeds one configured evaluation slot.

## Execute candidates with Slurm

Local subprocess execution is the default. One-process stages run directly;
multi-process stages use `mpiexec`. To launch stages as Slurm job steps,
select the backend explicitly:

```toml
[execution]
backend = "slurm"
```

The command must run inside an allocation created by `sbatch` or `salloc`. `hydroflow-opt` deliberately refuses to invoke `srun` without `SLURM_JOB_ID`, preventing candidates from becoming separately queued jobs. For example:

```bash
#!/bin/bash
#SBATCH --nodes=1
#SBATCH --ntasks=8
#SBATCH --cpus-per-task=1
#SBATCH --hint=nomultithread

hydroflow-opt optimize hydrofoil.toml
```

with:

```toml
[execution]
backend = "slurm"

[resources]
available_cpus = 8
concurrent_evaluations = 4
mpi_ranks = 2
threads_per_rank = 1
```

Every plugin supplies an ordered evaluation plan. Each stage is launched as an exclusive one-node step with its own process and thread shape. For example, a two-process, one-thread stage receives:

```text
srun --exclusive --nodes=1 --ntasks=2 --cpus-per-task=1 \
  --cpu-bind=cores --mpi=pmix
```

This lets Slurm place and account for preprocessing, solver, and postprocessing stages independently while seeing the actual MPI rank topology. The allocation may span nodes, but one stage must fit on one node Because successive stages may run on different nodes, their scratch directory must be visible from every allocated node. Partition, time limit, node count, and total allocation size remain properties of the outer Slurm job.

## Optimize with islands

Add an `[optimization]` table and use `optimize`:

```toml
[optimization]
islands = 4
population_size = 8
generations = 10
differential_weight = 0.8
crossover_rate = 0.9
topology = "fully_connected"
seed = 12345 # optional; generated and recorded when omitted
migrant_handling = "preserve" # or "evict"
```

```bash
hydroflow-opt optimize path/to/config.toml
```

By default, pygmo generates and evaluates each island's initial population. To reuse a database of pre-evaluated individuals, provide a JSON object whose values are `[parameter_vector, objective]` records:

```toml
[optimization]
initial_population_file = "start_db.json"
```

Each island samples `population_size` records without replacement using its derived reproducible seed. Islands sample independently. Seed records are stored in the optimization checkpoint but are not counted as new evaluations. `migrant_handling` selects whether pygmo preserves or evicts migrants after delivery.

Optimization runs write an atomic JSON checkpoint after initialization and after every generation. Resume an interrupted run using its stored effective configuration:

```bash
hydroflow-opt resume path/to/run-directory
```

Software and platform versions are recorded in `manifest.json`. Compatible version changes produce warnings when resuming rather than blocking the run; hydroflow-opt treats deterministic replay as best-effort. The selected execution backend is also recorded and restored automatically by `resume`.

The initial implementation supports pygmo differential evolution and a fully-connected archipelago. Islands use pygmo multiprocessing and therefore cannot exceed `resources.concurrent_evaluations`; this preserves the CPU budget even when each evaluation launches MPI ranks. The case plugin supplies parameter names, bounds, and decoding; optimization settings are per run.

## Write a case plugin

Publish an entry point in the `hydroflow_opt.cases` group. Its plugin object exposes `parameter_space(options)` and `evaluation_plan(candidate, paths, resources)`. The latter returns an ordered `EvaluationPlan` of `EvaluationStage` objects. Each stage declares a command, working directory, and portable `StageResources(processes, threads_per_process)` shape.

The local or Slurm backend adds the appropriate launcher. Plugins must never construct `mpiexec` or `srun` commands. The final stage writes the structured JSON result to `paths.result_path`; `hydroflow-opt` validates it and adds the recorded stage timings. A nonzero stage exit stops the plan and becomes a structured failed evaluation.

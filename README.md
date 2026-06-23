# flow-opt

`flow-opt` is a Linux/Python 3.11–3.13 orchestration package for
simulation-based optimization. It uses pygmo's island model, runs individual
case evaluations in isolated subprocesses, and does not depend on Slurm,
OpenFOAM, dtOO, or Pyro5.

Cases are supplied by installed plugins. The package includes a deterministic
`quadratic` case for laptop development and tests. A real case, such as
`flow-opt-hydrofoil`, depends on `flow-opt` rather than the reverse.

## Installation

Install with uv or pip on a supported Linux system:

```bash
uv sync --extra tests
# or
python -m pip install --editable '.[tests]'
```

`pygmo` is a required dependency. A simulation case may have additional
runtime prerequisites, but those must not be imported by `flow-opt` itself.

## Run explicit candidates

```bash
flow-opt check examples/quadratic.toml
flow-opt run examples/quadratic.toml
flow-opt inspect examples/runs/quadratic
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

Each candidate gets its own request, result, stdout, stderr, and scratch
directory under the run directory. The resource invariant is:

```text
concurrent_evaluations × mpi_ranks × threads_per_rank ≤ available_cpus
```

`flow-opt` refuses a configuration that violates it. A case may use the
allocated MPI rank count internally, but it must never choose global
concurrency or use an oversubscription flag.

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
```

```bash
flow-opt optimize path/to/config.toml
```

The initial implementation supports pygmo differential evolution and a
fully-connected archipelago. Islands use pygmo multiprocessing and therefore
cannot exceed `resources.concurrent_evaluations`; this preserves the CPU
budget even when each evaluation launches MPI ranks. The case plugin supplies
parameter names, bounds, and decoding; optimization settings are per run.

## Write a case plugin

Publish an entry point in the `flow_opt.cases` group. Its plugin object exposes
a `parameter_space(options)` method and a `worker_command(request, result)`
method. The command receives JSON paths and must write one structured result.
The worker protocol lets a future Slurm backend launch exactly the same case
worker with scheduler-owned resources.

## Development

```bash
python -m pytest
python -m ruff check .
```

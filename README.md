# hydroflow-opt

[![License](https://img.shields.io/pypi/l/hydroflow-opt?label=License)](https://opensource.org/licenses/MIT)
[![Build](https://github.com/thomasisensee/hydroflow-opt/actions/workflows/ci.yml/badge.svg)](https://github.com/thomasisensee/hydroflow-opt/actions)
[![codecov](https://codecov.io/gh/thomasisensee/hydroflow-opt/graph/badge.svg?token=KTY4LT5GNW)](https://codecov.io/gh/thomasisensee/hydroflow-opt)
[![pre-commit.ci](https://results.pre-commit.ci/badge/github/thomasisensee/hydroflow-opt/main.svg)](https://results.pre-commit.ci/latest/github/thomasisensee/hydroflow-opt/main)
[![PyPI](https://img.shields.io/pypi/v/hydroflow-opt?logo=pypi&logoColor=gold&label=PyPI)](https://pypi.org/project/hydroflow-opt)
[![Python](https://img.shields.io/pypi/pyversions/hydroflow-opt?logo=python&logoColor=gold&label=Python)](https://pypi.org/project/hydroflow-opt)

`hydroflow-opt` is a Linux/Python 3.11–3.13 orchestration package for
simulation-based optimization. It uses pygmo's island model and runs individual
case evaluations in isolated subprocesses.

Cases are supplied by installed plugins. The package includes a deterministic
`quadratic` case for laptop development and tests. A real case, such as
`hydrofoil-opt`, depends on `hydroflow-opt` rather than the reverse.

## Installation

Install with uv or pip on a supported Linux system:

```bash
uv sync --extra tests
# or
python -m pip install --editable '.[tests]'
```

`pygmo` is a required dependency. A simulation case may have additional
runtime prerequisites, but those must not be imported by `hydroflow-opt` itself.

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

Each candidate gets its own request, result, stdout, stderr, and scratch
directory under the run directory. The resource invariant is:

```text
concurrent_evaluations × mpi_ranks × threads_per_rank ≤ available_cpus
```

`hydroflow-opt` refuses a configuration that violates it. A case may use the
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
seed = 12345 # optional; generated and recorded when omitted
```

```bash
hydroflow-opt optimize path/to/config.toml
```

Optimization runs write an atomic JSON checkpoint after initialization and
after every generation. Resume an interrupted run using its stored effective
configuration:

```bash
hydroflow-opt resume path/to/run-directory
```

Software and platform versions are recorded in `manifest.json`. Compatible
version changes produce warnings when resuming rather than blocking the run;
hydroflow-opt treats deterministic replay as best-effort.

The initial implementation supports pygmo differential evolution and a
fully-connected archipelago. Islands use pygmo multiprocessing and therefore
cannot exceed `resources.concurrent_evaluations`; this preserves the CPU
budget even when each evaluation launches MPI ranks. The case plugin supplies
parameter names, bounds, and decoding; optimization settings are per run.

## Write a case plugin

Publish an entry point in the `hydroflow_opt.cases` group. Its plugin object exposes
a `parameter_space(options)` method and a `worker_command(request, result)`
method. The command receives JSON paths and must write one structured result.
The worker protocol lets a future Slurm backend launch exactly the same case
worker with scheduler-owned resources.

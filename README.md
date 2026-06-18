# flow-opt

`flow-opt` is a small orchestration layer for simulation-based optimization
workflows for Python 3.11 and newer. It is intended to make workflows easier to configure, run locally,
measure, and later connect to HPC execution backends.

The first version is deliberately backend-neutral. It does not depend on Slurm,
Pyro5, dtOO, OpenFOAM, or a cluster environment. Those systems can be connected
later through evaluator or execution-backend adapters.

## Installation

For development, use an editable install:

```bash
python -m pip install --editable .[tests]
```

or with uv:

```bash
uv sync --extra tests
```

## Minimal local run

The package includes a deterministic toy evaluator that can be run on any
machine:

```bash
flow-opt check examples/quadratic.toml
flow-opt run examples/quadratic.toml
flow-opt inspect examples/runs/quadratic
```

A workflow configuration names an evaluator, a run directory, optional resource
hints, and candidate parameters:

```toml
[run]
directory = "runs/quadratic"
scratch_directory = "runs/quadratic/scratch"

[evaluator]
name = "quadratic"

[resources]
cpus = 1

[[candidate]]
id = "baseline"
[candidate.parameters]
alpha = 1.0
beta = 2.0
```

`flow-opt run` writes a copied config, `results.jsonl`, and `summary.json` to
the run directory.

## Design direction

Workflow-specific code should implement the `CaseEvaluator` interface. The core
package handles candidate records, execution context, result records, run
directories, and local execution. HPC launchers and distributed execution are
future adapters, not assumptions in the core API.

## Development

Run tests with:

```bash
python -m pytest
```

Run linting with:

```bash
python -m ruff check .
```

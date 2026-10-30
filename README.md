# hydroflow-opt

[![License](https://img.shields.io/pypi/l/hydroflow-opt?label=License)](https://opensource.org/licenses/MIT)
[![Build](https://github.com/thomasisensee/hydroflow-opt/actions/workflows/ci.yml/badge.svg)](https://github.com/thomasisensee/hydroflow-opt/actions)
[![codecov](https://codecov.io/gh/thomasisensee/hydroflow-opt/graph/badge.svg?token=KTY4LT5GNW)](https://codecov.io/gh/thomasisensee/hydroflow-opt)
[![pre-commit.ci](https://results.pre-commit.ci/badge/github/thomasisensee/hydroflow-opt/main.svg)](https://results.pre-commit.ci/latest/github/thomasisensee/hydroflow-opt/main)
[![PyPI](https://img.shields.io/pypi/v/hydroflow-opt?logo=pypi&logoColor=gold&label=PyPI)](https://pypi.org/project/hydroflow-opt)
[![Python](https://img.shields.io/pypi/pyversions/hydroflow-opt?logo=python&logoColor=gold&label=Python)](https://pypi.org/project/hydroflow-opt)

`hydroflow-opt` is a Linux/Python 3.11–3.14 orchestration package for simulation-based optimization. It uses [pygmo's](https://github.com/esa/pygmo2) island model, runs concurrent evaluations locally or within Slurm allocations, and supports checkpoints and resume.

Simulation cases are supplied by installed plugins. The package includes a deterministic `quadratic` case for development and tests; simulation plugins may require additional runtime dependencies.

## Installation

From a source checkout, install with uv or pip:

```bash
uv sync --extra tests
# or
python -m pip install --editable '.[tests]'
```

## Quick start

Run the included example from the repository root with the installed environment active:

```bash
hydroflow-opt check examples/quadratic.toml
hydroflow-opt run examples/quadratic.toml
hydroflow-opt inspect examples/runs/quadratic
```

For optimization, use `hydroflow-opt optimize CONFIG`. Continue an interrupted optimization with `hydroflow-opt resume RUN_DIRECTORY`.

## Documentation

See the [full documentation](https://hydroflow-opt.readthedocs.io/) for setup and usage details:

- [Overview](doc/overview.md): responsibilities and run modes.
- [Configuration and commands](doc/configuration.md): TOML settings, resource budgets, and debugging saved candidates.
- [Execution model](doc/execution-model.md): staged evaluations and local/Slurm execution.
- [Optimization](doc/optimization.md): islands, concurrent initial populations, checkpoints, and resume.
- [Node-local scratch](doc/node-local-scratch.md): placement and scratch storage across Slurm nodes.
- [Output layout](doc/output-layout.md): results, logs, and run metadata.
- [Writing plugins](doc/writing-plugins.md): the case plugin contract.

# Welcome to flow-opt

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![GitHub Workflow Status](https://github.com/thomasisensee/flow-opt/actions/workflows/ci.yml/badge.svg)](https://github.com/thomasisensee/flow-opt/actions/workflows/ci.yml)
[![Documentation Status](https://readthedocs.org/projects/flow-opt/badge/)](https://flow-opt.readthedocs.io/)
[![codecov](https://codecov.io/github/thomasisensee/flow-opt/graph/badge.svg?token=DRJB38CIZI)](https://codecov.io/github/thomasisensee/flow-opt)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/thomasisensee/flow-opt/main.svg)](https://results.pre-commit.ci/latest/github/thomasisensee/flow-opt/main)
![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12%20|%203.13%20|%203.14-blue)


## Installation

The Python package `flow_opt` can be installed from PyPI:

```
python -m pip install flow_opt
```

## Development installation

If you want to contribute to the development of `flow_opt`, we recommend
the following editable installation from this repository:

```
git clone git@github.com:thomasisensee/flow-opt.git
cd flow-opt
python -m pip install --editable .[tests]
```

Having done so, the test suite can be run using `pytest`:

```
python -m pytest
```

## Acknowledgments

This repository was set up using the [SSC Cookiecutter for Python Packages](https://github.com/ssciwr/cookiecutter-python-package).

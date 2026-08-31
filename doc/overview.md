# Overview

`hydroflow-opt` provides reusable orchestration for simulation-based optimization. It owns:

- configuration and resource validation;
- concurrent candidate evaluation;
- local and Slurm command launching;
- per-stage logs and timings;
- structured results;
- pygmo island optimization, checkpoints, and resume.

A case plugin owns:

- parameter names and bounds;
- the ordered commands needed to evaluate a candidate;
- case-specific runtime dependencies and files;
- calculation of the objective and metadata.

The core does not import a plugin's solver, meshing, or geometry libraries. Heavy dependencies are loaded only by the plugin's stage processes.

## Two run modes

`hydroflow-opt run CONFIG` evaluates the explicit `[[candidate]]` entries in a configuration file.

`hydroflow-opt optimize CONFIG` asks pygmo to generate candidates from the plugin's parameter space. Optimization runs are checkpointed and can be continued with `hydroflow-opt resume RUN_DIRECTORY`.


## Main data flow

```text
TOML configuration
       │
       ├── case plugin ──> parameter space
       │
       └── candidate ──> evaluation plan
                              │
                              ├── stage 1
                              ├── stage 2
                              └── final stage ──> result.json
                                      │
                                      ▼
                              validated result and summary
```

The built-in `quadratic` case is a portable example. Real cases are separate packages registered through Python entry points.

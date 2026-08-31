# Configuration

Paths are resolved relative to the TOML file. Unknown case-specific values can be placed below `[case.options]` and are passed to the plugin.

## Explicit candidate run

```toml
[run]
directory = "runs/example"                 # required
scratch_directory = "runs/example/scratch" # default: <directory>/scratch

[case]
name = "example"                           # required

[case.options]
variant = "baseline"

[execution]
backend = "local"                          # local or slurm; default: local

[resources]
available_cpus = 4                         # default: 1
concurrent_evaluations = 2                 # default: 1
mpi_ranks = 2                              # default: 1
threads_per_rank = 1                       # default: 1

[[candidate]]
id = "baseline"                            # default: candidate-N

[candidate.parameters]
x = 0.25
y = -0.5
```

`run` requires at least one candidate. Candidate parameter names are validated by the case only when it constructs or executes its plan.

## Optimization

Replace explicit candidates with:

```toml
[optimization]
islands = 4                                 # default: 1
population_size = 8                         # minimum: 5; default: 8
generations = 10                            # default: 1
differential_weight = 0.8                   # 0 ≤ value ≤ 2
crossover_rate = 0.9                        # 0 ≤ value ≤ 1
topology = "fully_connected"                # only supported value
seed = 12345                                # optional unsigned 32-bit integer
migrant_handling = "preserve"               # preserve or evict
initial_population_file = "population.json" # optional
```

The resource constraints are:

```text
concurrent_evaluations × mpi_ranks × threads_per_rank ≤ available_cpus islands ≤ concurrent_evaluations
```

## Commands

| Command | Purpose |
|---|---|
| `hydroflow-opt check CONFIG` | Parse configuration and resolve the case plugin. |
| `hydroflow-opt run CONFIG` | Evaluate explicit candidates. |
| `hydroflow-opt optimize CONFIG` | Start a new optimization. |
| `hydroflow-opt resume RUN_DIR` | Continue a checkpointed optimization. |
| `hydroflow-opt inspect RUN_DIR` | Print a completed run summary. |

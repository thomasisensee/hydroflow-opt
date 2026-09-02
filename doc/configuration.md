# Configuration

Paths are resolved relative to the TOML file. Unknown case-specific values can be placed below `[case.options]` and are passed to the plugin.

`run.scratch_directory` additionally accepts `$NAME` and `${NAME}` environment-variable placeholders. Variables are resolved when `run`, `optimize`, or `resume` starts; unset or empty variables are errors. Use `$$` for a literal dollar sign. Shell expressions, command substitution, and defaults such as `${NAME:-value}` are not supported. A relative path after substitution remains relative to the original TOML file.

`hydroflow-opt check` validates the template without requiring its variables to be set, so configuration can be checked outside a batch allocation.

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

For a single-node cluster job, persistent output and node-local working data can be separated with:

```toml
[run]
directory = "/persistent/workspace/runs/example"
scratch_directory = "${TMPDIR}/example"
```

Use a distinct subdirectory if several hydroflow-opt runs share one allocation.

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

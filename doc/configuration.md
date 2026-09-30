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
# stage_timeout_seconds = 1800             # optional per-stage budget

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
| `hydroflow-opt replay-config REQUEST OUTPUT` | Generate an editable single-candidate TOML from `request.json`. |
| `hydroflow-opt run CONFIG` | Evaluate explicit candidates. |
| `hydroflow-opt optimize CONFIG` | Start a new optimization. |
| `hydroflow-opt resume RUN_DIR` | Continue a checkpointed optimization. |
| `hydroflow-opt inspect RUN_DIR` | Print a completed run summary. |

## Debugging a saved candidate

Use the evaluation's `request.json` to generate a new single-candidate config:

```bash
hydroflow-opt replay-config <path-to-run-directory>/request.json debug-001.toml
hydroflow-opt check debug-001.toml
# With the case environment and, for Slurm, a suitable allocation:
hydroflow-opt run debug-001.toml
```

The helper copies the candidate ID and all parameters, case options, execution backend and stage timeout. It retains MPI ranks, threads per rank, and memory budget, but sets `concurrent_evaluations = 1` and reduces `available_cpus` to `mpi_ranks * threads_per_rank`. Optimization settings are omitted.

The filename determines the run-directory name. An existing output file or populated destination run directory is rejected; choose another filename for another attempt. Generating the TOML does not create the run directory, load the case plugin, or start any simulation.

Review and edit the TOML before execution. For serial debugging, set `mpi_ranks = 1` and adjust `available_cpus` to `threads_per_rank`. Slurm runs still need a batch allocation sized for the resulting resources. Case option values are copied literally, so any cluster-specific or relative paths in those values may need editing when moving to another environment.

Legacy requests without execution settings default to local execution. Slurm requests must contain a memory budget; the helper does not guess one. Values that TOML cannot represent (for example JSON null in case options) produce an error rather than being silently removed.

`run` executes the entire workflow, including mesh generation. To reuse a saved mesh for solver-only debugging, run the solver directly in a separate copy of the saved case. Matching terminal results, including failures, are cached if the same generated run is executed again; generate a new TOML with a different filename for a fresh attempt.

## Stage time limits

Set an optional positive integer budget in seconds:

```toml
[execution]
backend = "slurm"                  # also supported with "local"
stage_timeout_seconds = 1800
```

The budget applies independently to every stage, including preparation and finalization. Omit it for no framework-imposed limit. Zero, negative values, booleans, strings, and fractional seconds are rejected. `1800` is an example, not an estimate of CFD runtime; choose a generous budget for your workload. There is no inactivity detection or automatic retry.

With Slurm, each `srun` gets `--time` rounded **up to whole minutes** (61 seconds becomes two minutes). Slurm terminates the step on expiry using its configured termination grace and overtime policy. This bounds step execution, not waiting for resources. The outer `#SBATCH --time` still limits the whole allocation. A nonzero launcher exit records a failed evaluation; the Slurm stderr log contains the cause. An exit code alone is not classified as proof of a timeout.

Locally, the budget starts when the stage process is launched. On expiry, hydroflow-opt sends `SIGTERM` to its process group, waits five seconds, then sends `SIGKILL` to remaining group members and reaps the launcher. This covers local MPI processes in the group; descendants that detach into another session group are outside this cleanup. Cleanup time is additional to the configured budget. The result explicitly reports the stage timeout.

Stdout and stderr go directly to stage log files as the application emits them (application-level buffering can still delay output). Stage metadata records the configured budget. Failure skips remaining stages for that candidate, preserves diagnostics, and uses the existing optimization penalty so subsequent evaluations can continue.

To check the cluster's step-timeout behavior, run this inside a small Slurm
allocation with more than two minutes remaining:

```bash
srun --exclusive --nodes=1 --ntasks=1 --cpus-per-task=1 --mem=256M --time=1 sleep 120 && exit 1
srun --exclusive --nodes=1 --ntasks=1 --cpus-per-task=1 --mem=256M true
```

The first step should report a time-limit failure after about one minute; the second should complete in the same allocation. Confirm `TIMEOUT` for the first step with `sacct`; a different failure needs investigation. Then run a small hydroflow-opt case with a representative budget before scaling up.

## Slurm memory budget

Slurm configurations require an explicit positive integer under `[resources]`:

```toml
[resources]
memory_mib_per_evaluation = 1536
```

This is the total memory reservation in MiB for one stage across all its MPI processes, not memory per ran. Every stage requests the same budget through `srun --mem=1536M`, overriding inherited batch memory requests. Since stages within an evaluation run sequentially, their reservations are not summed. Local execution accepts and records this setting but does not enforce it.

`1536` is a trial value, not a measured requirement for every design. Budget for the largest stage and leave headroom for Python workers and scheduler processes. For example, 32 simultaneous stages at 1536 MiB reserve 48 GiB on a node allocated 64 GiB. `#SBATCH --mem` still specifies memory **per node**.

When positive numeric `SLURM_MEM_PER_NODE` and node counts are available, hydroflow-opt checks `nodes * floor(memory_per_node / budget)` against `concurrent_evaluations` before starting workers. Unknown or zero-valued allocation information skips this check. Passing it does not guarantee CPU placement or sufficient memory headroom; normal scheduling waits can remain.

Older Slurm TOMLs must add the field. Resume reads its saved configuration, not the original TOML; supply or change its budget with:

```bash
hydroflow-opt resume runs/tistos-64 --memory-mib-per-evaluation 1536
```

The override is Slurm-only, validated before persisting, and saved atomically with an updated configuration hash and provenance recording the old/new budgets and previous hash. Later resumes use the saved value. Completed outcomes remain reusable; interrupted evaluations rerun, while previously recorded failed outcomes retain the existing reuse behavior. Completed runs remain a no-op.

Before scaling up, run two islands on one node with adequate CPUs and memory. Use `sacct` to verify overlapping steps with the configured memory reservation, and check memory usage and failures before increasing concurrency. Stage timings still include time spent waiting for Slurm to launch the step.

# Node-local scratch across a Slurm allocation

Use `run.scratch_mode = "node_local"` to keep each candidate on one assigned
node from preparation through finalization. Multiple candidates can share each
node. Each individual solver stage must still fit on one node.

```toml
[run]
directory = "/shared/workspace/runs/optimization-001"
scratch_mode = "node_local"
scratch_directory = "${TMPDIR}/hydroflow-opt"

[execution]
backend = "slurm"

[resources]
available_cpus = 8
concurrent_evaluations = 4
mpi_ranks = 2
threads_per_rank = 1
memory_mib_per_evaluation = 2048
```

For these illustrative budgets, an allocation can request:

```bash
#!/bin/bash
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=4
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=01:00:00

# Activate the Python/CFD environment here.
hydroflow-opt optimize /shared/path/optimization.toml
```

Do not wrap the controller in `srun`. The controller and its pygmo workers run
on the batch node; hydroflow-opt starts and pins the individual stage steps.
All nodes must see the Python environment, plugins, CFD executables, and run
directory at the same paths. Size memory and ranks for your actual cases;
the example budgets are not production CFD recommendations.

## Placement and resources

The invocation owns a local IPC coordinator shared by candidate threads and
pygmo processes. It discovers nodes using `scontrol show hostnames` and uses
`SLURM_JOB_CPUS_PER_NODE` plus `SLURM_MEM_PER_NODE` (or `SLURM_MEM_PER_CPU`) to
compute candidate slots. Compressed CPU counts such as `32(x2)` are supported.
Missing/ambiguous budgets, unlimited (`--mem=0`) budgets, and heterogeneous
Slurm job components are rejected in this mode.

The per-node slot count is the smaller of allocated CPUs divided by CPUs per
candidate and allocated memory divided by memory per candidate, rounded down.
Requested concurrency must fit the allocation. Slots are assigned round-robin,
subject to the global concurrency cap, and held throughout an evaluation,
including serial stages. These are reservations among this invocation's
candidates, not idle Slurm resource reservations. Run one controller per
allocation; unrelated steps can compete with these steps.

Node-local scratch defaults to `${TMPDIR}/hydroflow-opt`. Expansion happens
on the assigned node, and the result must be an absolute path beneath its
`$TMPDIR`. The backend appends invocation, candidate, and attempt identifiers.
It verifies visibility of a shared token before running the plugin. It launches
`srun` from shared storage and sets the remote stage directory with `--chdir`.
There is no controller-side creation or inspection of the remote scratch tree.

## Results, diagnostics, and recovery

The shared run directory contains requests, results, optimization checkpoints,
stdout/stderr, and timings. Each evaluation also has `placement.json`; stage
metadata includes the actual hostname, rank placement, and Slurm step ID.
Concrete node/path assignments are per-attempt metadata, not configuration.

After a successful evaluation, the owned scratch attempt is removed. Both CFD
plugins already delete their completed CFD cases after writing objectives.
Successful meshes/fields are not retained by this feature.

After failure, `diagnostics.tar.gz` contains logs (`*.log`, `log.*`), JSON/XML
state, OpenFOAM `system` dictionaries, and top-level `constant` dictionaries.
Meshes, processor directories, numeric time directories, inlet datasets, and
symlink targets are excluded. Collection is capped at 100 MiB uncompressed and
10 MiB per file: oversized logs retain their tails, oversized configuration
files are omitted. `diagnostics-manifest.json` records omissions/truncation.
`diagnostics.json` reports export success or failure. Collection has a separate
120-second limit and does not replace the original evaluation error.
Failed scratch remains until the allocation ends. Allow enough local disk for
concurrent cases and accumulated failures; disk capacity is not reserved.

Completed outcomes are reusable without their scratch. Resume creates a fresh
placement coordinator and recomputes interrupted candidates from preparation.
There is no stage-level CFD restart or automatic retry of terminal failures.
Use replay to rerun a failed candidate; generated replay configurations use
fresh shared scratch by default. A lost node may prevent diagnostic export.
Cancellation targets only owned job steps; uncertain termination quarantines
scheduling instead of releasing resources for more work.

`shared` remains the default mode. Existing configurations, including one-node
`$TMPDIR` runs, retain their behavior. Multi-node shared-mode runs still need
scratch visible from every node. For programmatic node-local execution, the
normal runner functions manage the session. Custom cases can use
`hydroflow_opt.backends.node_local.node_local_session(config, case)` and pass
its backend to `run_local`.

## Cluster acceptance

Local tests simulate node-specific scratch and actual spawned processes. Real
Slurm acceptance remains required before production use.

Inside the two-node allocation above, run from the hydroflow-opt checkout:

```bash
python examples/node_local_smoke.py /shared/workspace/smoke-success-001
python examples/node_local_smoke.py /shared/workspace/smoke-failure-001 --fail-one
```

Use new paths for each invocation. The smoke test runs four candidates with
five stages each and two ranks per solver stage. It asserts two candidates
per node, consistent placement across stages/ranks, and failure diagnostic
export. Verify the shared outputs remain accessible after the batch job ends.

Then run the plugins' `*_optimization_node_local.toml` examples with suitable
resource budgets. For scientific validation, compare fixed candidate parameters
and objectives against shared-scratch runs; record preparation, solve, and
finalization timings and peak disk usage at representative concurrency.

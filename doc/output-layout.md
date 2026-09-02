# Output layout

An explicit run produces:

```text
run-directory/
├── config.toml
├── results.jsonl
├── summary.json
└── evaluations/
    └── <candidate-id>/
        ├── request.json
        ├── result.json
        ├── attempts/                 # only after stale attempts
        └── stages/
            ├── 01-<stage-name>/
            │   ├── metadata.json
            │   ├── stdout.log
            │   └── stderr.log
            └── ...
```

The configured scratch root contains one plugin-owned directory per candidate:

```text
scratch-directory/
└── <candidate-id>/
```

Plugins decide which case files remain after success. Failed evaluations should normally retain diagnostic files.

When the scratch root uses node-local storage such as `$TMPDIR`, its contents normally disappear after the allocation. Structured results, outcomes, metadata, and logs from completed stages remain in the persistent run directory. Hydroflow-opt does not copy complete failed simulation cases back automatically. Because stage output is captured and written after the command exits, output from a stage interrupted by allocation termination may also be unavailable.

## Evaluation files

`request.json` contains the candidate, case selection, paths, resources, backend, and optional optimization context.

`result.json` contains:

```json
{
  "candidate_id": "baseline",
  "status": "success",
  "objective": 1.25,
  "timings": {"prepare": 2.1, "solve": 34.7, "finalize": 0.2},
  "metadata": {},
  "error": null
}
```

Each stage's `metadata.json` records its declared command, backend launch command, working directory, resources, start time, duration, and return code. The log files are currently written when the stage exits rather than streamed during execution.

`results.jsonl` contains one normalized result per line. `summary.json` records the succeeded, failed, and total counts.

## Optimization additions

Optimization evaluations also contain `evaluation.json` and `outcome.json`.
The run adds:

```text
run-directory/
├── manifest.json
└── optimization/
    ├── checkpoint.json
    ├── history.jsonl
    ├── initial-population.json       # when supplied
    ├── final-populations.json
    └── champions.json
```

`manifest.json` stores the effective configuration, parameter space, provenance, evaluation identifiers, and completion status. Scratch-directory templates are stored without allocation-specific expansion, while every start or resume provenance entry records the concrete path used. A resumed run therefore resolves variables such as `$TMPDIR` again for its new allocation. `checkpoint.json` is the authoritative resume state.

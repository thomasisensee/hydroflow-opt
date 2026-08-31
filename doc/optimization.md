# Optimization

`hydroflow-opt` currently provides single-objective differential evolution through pygmo. A plugin supplies the ordered parameter names and physical bounds; pygmo supplies vectors within those bounds.

## Islands and concurrency

Each island has its own population and performs one differential-evolution generation at a time. Islands use pygmo multiprocessing and exchange migrants through a fully connected topology.

```text
optimization.islands ≤ resources.concurrent_evaluations
```

Candidate identifiers record their origin, for example:

```text
island-002-initial-004
island-002-generation-000003-trial-004
```

A failed evaluation receives an internal penalty objective of `1e12`, while its persisted result remains marked as failed.

## Checkpoints and resume

A new optimization requires an empty run directory. Hydroflow-opt writes a checkpoint after each island initialization and completed generation. Resume uses the stored effective configuration rather than the original TOML file:

```bash
hydroflow-opt resume path/to/run
```

The manifest records Python, platform, backend, core, pygmo, NumPy, and plugin versions. Version changes on resume produce warnings. Reproducibility is best-effort because solver and platform behavior may still differ.

## Initial populations

`initial_population_file` can provide previously evaluated designs. It is a JSON object whose values are `[parameter_vector, objective]` records:

```json
{
  "design-17": [[0.2, -0.4], 0.2],
  "design-42": [[0.1, 0.3], 0.1]
}
```

The file must contain at least `population_size` finite, in-bounds records. Each island samples independently using its derived seed. The file is copied into the run directory so resume does not depend on the original location.

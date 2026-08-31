# hydroflow-opt

`hydroflow-opt` orchestrates staged simulation evaluations and pygmo
optimizations. Simulation-specific code is supplied by installed case plugins.

```{toctree}
:maxdepth: 2

overview
execution-model
optimization
writing-plugins
configuration
output-layout
api
```

## First run

```bash
hydroflow-opt check examples/quadratic.toml
hydroflow-opt run examples/quadratic.toml
hydroflow-opt inspect examples/runs/quadratic
```

See {doc}`overview` for the division of responsibilities and
{doc}`writing-plugins` for the plugin contract.

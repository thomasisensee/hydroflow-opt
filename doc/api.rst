API reference
=============

Only the public orchestration contracts are listed here. Case-specific runtime functions belong to their plugin documentation.

Models
------

.. autoclass:: hydroflow_opt.Candidate
   :members:

.. autoclass:: hydroflow_opt.ParameterSpace
   :members:

.. autoclass:: hydroflow_opt.ResourceRequest
   :members:

.. autoclass:: hydroflow_opt.StageResources
   :members:

.. autoclass:: hydroflow_opt.EvaluationPaths
   :members:

.. autoclass:: hydroflow_opt.EvaluationStage
   :members:

.. autoclass:: hydroflow_opt.EvaluationPlan
   :members:

.. autoclass:: hydroflow_opt.EvaluationResult
   :members:

.. autoclass:: hydroflow_opt.EvaluationStatus
   :members:

Plugin contract
---------------

.. autoclass:: hydroflow_opt.CasePlugin
   :members:

.. autofunction:: hydroflow_opt.case_from_name

.. autofunction:: hydroflow_opt.results.write_result

Configuration
-------------

.. autoclass:: hydroflow_opt.FlowOptConfig
   :members:

.. autoclass:: hydroflow_opt.ExecutionConfig
   :members:

.. autoclass:: hydroflow_opt.OptimizationConfig
   :members:

.. autofunction:: hydroflow_opt.load_config

Execution
---------

.. autoclass:: hydroflow_opt.SubprocessBackend
   :members:

.. autoclass:: hydroflow_opt.SlurmBackend
   :members:

Runs
----

.. autoclass:: hydroflow_opt.RunSummary
   :members:

.. autofunction:: hydroflow_opt.run_local

.. autofunction:: hydroflow_opt.run_optimization

.. autofunction:: hydroflow_opt.resume_optimization

.. autofunction:: hydroflow_opt.runner.inspect_run

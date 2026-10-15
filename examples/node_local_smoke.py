"""Check two-node scratch affinity with five stages and two ranks per solve.

Run inside a two-node allocation, from the batch shell (not through srun):
    python examples/node_local_smoke.py /shared/workspace/smoke-001
Use --fail-one with a fresh run directory to exercise diagnostic export.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from hydroflow_opt import (
    Candidate,
    EvaluationPlan,
    EvaluationStage,
    FlowOptConfig,
    ResourceRequest,
    StageResources,
    run_local,
)
from hydroflow_opt.backends.node_local import node_local_session
from hydroflow_opt.cases import QuadraticCase
from hydroflow_opt.config import ExecutionConfig
from hydroflow_opt.models import BackendKind


class ProbeCase(QuadraticCase):
    """Exercise node-local handoffs without loading CFD software."""

    def __init__(self, fail_one=False):
        """Optionally fail one solver stage to test diagnostic export."""
        self.fail_one = fail_one

    def evaluation_plan(self, candidate, paths, resources):
        """Match the plugins' five-stage, serial/MPI execution pattern."""
        stages = []
        for name in (
            "prepare",
            "solve-laminar",
            "configure-turbulent",
            "solve-turbulent",
            "finalize",
        ):
            command = (
                sys.executable,
                str(Path(__file__).resolve()),
                "stage",
                name,
                str(paths.request_path),
                str(paths.result_path),
                "fail" if self.fail_one else "pass",
            )
            stages.append(
                EvaluationStage(
                    name,
                    command,
                    paths.scratch_dir,
                    StageResources(resources.mpi_ranks)
                    if name.startswith("solve-")
                    else StageResources(),
                )
            )
        return EvaluationPlan(tuple(stages))


def stage():
    """Check shared scratch continuity on every rank of every stage."""
    name, request_path, result_path, mode = sys.argv[2:]
    request = json.loads(Path(request_path).read_text())
    node = os.environ["SLURMD_NODENAME"]
    marker = Path("node.txt")
    if name == "prepare":
        marker.write_text(node)
        Path("log.probe").write_text("node-local diagnostic export probe\n")
        Path("state.json").write_text(json.dumps({"node": node}))
    else:
        assert marker.read_text() == node, "candidate moved between nodes"
    time.sleep(1)
    if (
        mode == "fail"
        and request["candidate"]["id"] == "candidate-0"
        and name == "solve-laminar"
    ):
        raise SystemExit(7)
    if name == "finalize":
        Path(result_path).write_text(
            json.dumps(
                {
                    "candidate_id": request["candidate"]["id"],
                    "status": "success",
                    "objective": 0,
                    "timings": {},
                    "metadata": {},
                }
            )
        )


def main():
    """Run and verify a four-candidate, two-node probe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--fail-one", action="store_true")
    args = parser.parse_args()
    directory = args.run_directory.resolve()
    if directory.exists():
        parser.error("use a fresh shared run directory")
    if os.environ.get("SLURM_JOB_NUM_NODES") != "2":
        parser.error("the smoke test requires exactly two allocated nodes")
    config = FlowOptConfig(
        run_dir=directory,
        scratch_dir=Path("${TMPDIR}/hydroflow-opt"),
        scratch_dir_template="${TMPDIR}/hydroflow-opt",
        scratch_mode="node_local",
        case_name="quadratic",
        candidates=tuple(
            Candidate(f"candidate-{i}", {"x": float(i)}) for i in range(4)
        ),
        resources=ResourceRequest(
            available_cpus=8,
            concurrent_evaluations=4,
            mpi_ranks=2,
            memory_mib_per_evaluation=2048,
        ),
        execution=ExecutionConfig(BackendKind.SLURM, 120),
    )
    with node_local_session(config, ProbeCase(args.fail_one)) as backend:
        summary = run_local(config, backend=backend)
    expected_failures = int(args.fail_one)
    assert summary.failed == expected_failures
    assert summary.succeeded == 4 - expected_failures
    assignments = []
    for candidate in config.candidates:
        evaluation = directory / "evaluations" / candidate.id
        assigned = json.loads((evaluation / "placement.json").read_text())
        assignments.append(assigned["assigned_node"])
        for record_path in (evaluation / "stages").glob("*/metadata.json"):
            record = json.loads(record_path.read_text())
            assert record["rank_placements"]
            assert all(
                p["node"] == assigned["assigned_node"]
                for p in record["rank_placements"]
            )
    assert len(set(assignments)) == 2
    assert all(assignments.count(node) == 2 for node in set(assignments))
    if args.fail_one:
        assert (
            directory / "evaluations/candidate-0/diagnostics.tar.gz"
        ).is_file()
    print(f"Verified two-node affinity; results: {directory}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["stage"]:
        stage()
    else:
        main()

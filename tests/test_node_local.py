import json
import multiprocessing
import os
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from hydroflow_opt import case_from_name, load_config, run_local
from hydroflow_opt.backends.node_local import _Attempt, node_local_session
from hydroflow_opt.backends.placement import Allocator, allocation_slots
from hydroflow_opt.backends.remote import bootstrap, collect
from hydroflow_opt.config import resolve_runtime_config
from hydroflow_opt.replay import create_replay_config
from hydroflow_opt.runner import resume_optimization, run_optimization
from tests.test_hydroflow_opt import add_optimization, write_config


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    helper = Path(__file__).parent / "helpers" / "fake_slurm.py"
    for name in ("scontrol", "srun", "scancel", "squeue"):
        executable = bin_dir / name
        executable.write_text(f"#!{sys.executable}\n" + helper.read_text())
        executable.chmod(0o755)
    for name, value in {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_SLURM_ROOT": str(tmp_path),
        "SLURM_JOB_ID": "123",
        "SLURM_JOB_NODELIST": "node-[a,b]",
        "SLURM_JOB_NUM_NODES": "2",
        "SLURM_JOB_CPUS_PER_NODE": "4(x2)",
        "SLURM_MEM_PER_NODE": "4096",
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SLURM_HET_SIZE", raising=False)
    monkeypatch.delenv("SLURM_HET_GROUP", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    return shared


def node_config(shared, concurrent=2):
    path = write_config(
        shared,
        backend="slurm",
        concurrent=concurrent,
        scratch_directory="${TMPDIR}/hydroflow-opt",
    )
    path.write_text(
        path.read_text().replace("[run]", '[run]\nscratch_mode = "node_local"')
    )
    return load_config(path)


def test_config_is_deferred_and_validated(tmp_path, monkeypatch):
    monkeypatch.delenv("TMPDIR", raising=False)
    config = node_config(tmp_path)
    assert resolve_runtime_config(config) is config
    with pytest.raises(ValueError, match="scratch_mode"):
        replace(config, scratch_mode="unknown")
    from hydroflow_opt.config import ExecutionConfig

    with pytest.raises(ValueError, match="Slurm"):
        replace(config, execution=ExecutionConfig())


def test_default_node_local_template(tmp_path):
    path = write_config(tmp_path, backend="slurm")
    path.write_text(
        "\n".join(
            line
            for line in path.read_text()
            .replace("[run]", '[run]\nscratch_mode = "node_local"')
            .splitlines()
            if not line.startswith("scratch_directory")
        )
    )
    assert load_config(path).scratch_dir_template == "${TMPDIR}/hydroflow-opt"


def test_allocation_cpu_memory_limits(cluster, monkeypatch):
    config = node_config(cluster, concurrent=4)
    assert allocation_slots(config) == {"node-a": 2, "node-b": 2}
    monkeypatch.setenv("SLURM_MEM_PER_NODE", "2000")
    with pytest.raises(ValueError, match="concurrency"):
        allocation_slots(config)
    monkeypatch.delenv("SLURM_MEM_PER_NODE")
    monkeypatch.setenv("SLURM_MEM_PER_CPU", "1024")
    assert allocation_slots(config) == {"node-a": 2, "node-b": 2}
    monkeypatch.setenv("SLURM_JOB_CPUS_PER_NODE", "4(x3)")
    with pytest.raises(ValueError, match="counts"):
        allocation_slots(config)


def _lease_worker(connection, queue, release):
    allocator = connection.connect()
    token, node = allocator.acquire(os.getpid())
    queue.put(node)
    release.wait(15)
    allocator.release(token)


def test_spawned_workers_share_capacity(cluster):
    config = node_config(cluster, concurrent=4)
    ctx = multiprocessing.get_context("spawn")
    with node_local_session(config, case_from_name("quadratic")) as backend:
        queue = ctx.Queue()
        release = ctx.Event()
        workers = [
            ctx.Process(
                target=_lease_worker, args=(backend.connection, queue, release)
            )
            for _ in range(5)
        ]
        try:
            for worker in workers:
                worker.start()
            nodes = [queue.get(timeout=15) for _ in range(4)]
            assert nodes.count("node-a") == nodes.count("node-b") == 2
            assert backend.connection.connect().active()[0] == 4
            release.set()
            assert queue.get(timeout=15) in {"node-a", "node-b"}
            for worker in workers:
                worker.join(timeout=15)
                assert worker.exitcode == 0
            assert backend.connection.connect().active()[0] == 0
        finally:
            release.set()
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                worker.join(timeout=5)
            queue.close()


def test_quarantined_allocator_never_reuses_slots():
    allocator = Allocator({"n": 1}, 1)
    lease, _ = allocator.acquire(os.getpid())
    allocator.abort("uncertain termination")
    allocator.release(lease)
    with pytest.raises(RuntimeError, match="uncertain"):
        allocator.acquire(os.getpid())


def test_remote_runs_cache_and_replay(cluster):
    config = node_config(cluster)
    assert run_local(config).succeeded == 2
    placements = [
        json.loads(path.read_text())
        for path in config.run_dir.glob("evaluations/*/placement.json")
    ]
    assert {p["assigned_node"] for p in placements} == {"node-a", "node-b"}
    assert all(not Path(p["scratch_dir"]).exists() for p in placements)
    stages = list(config.run_dir.glob("evaluations/*/stages/*/metadata.json"))
    for path in stages:
        record = json.loads(path.read_text())
        assert record["rank_placements"][0]["node"] == record["assigned_node"]
    launches = len(list((cluster.parent / "launches").iterdir()))
    assert run_local(config).succeeded == 2
    assert len(list((cluster.parent / "launches").iterdir())) == launches
    output = cluster / "replay.toml"
    create_replay_config(config.run_dir / "evaluations/a/request.json", output)
    assert load_config(output).scratch_mode == "shared"


def test_bootstrap_failure_releases_slots(cluster, monkeypatch):
    config = node_config(cluster)
    monkeypatch.setenv("FAIL_BOOTSTRAP", "1")
    with node_local_session(config, case_from_name("quadratic")) as backend:
        result = backend.evaluate(config.candidates[0])
        assert result.error
        assert backend.connection.connect().active() == (0, [])
    diagnostic = config.run_dir / "evaluations/a/diagnostics.json"
    assert json.loads(diagnostic.read_text())["status"] == "unavailable"


def test_resume_new_allocation_reuses_outcomes(cluster, monkeypatch):
    pytest.importorskip("pygmo")
    config = node_config(cluster)
    path = cluster / "run.toml"
    add_optimization(path, islands=2, generations=2, seed=123)
    config = load_config(path)
    import hydroflow_opt.runner as runner

    save = runner._save_checkpoint

    def interrupt(run_dir, checkpoint):
        save(run_dir, checkpoint)
        if checkpoint["history"]:
            raise RuntimeError("interrupt after generation")

    with monkeypatch.context() as patch:
        patch.setattr(runner, "_save_checkpoint", interrupt)
        with pytest.raises(RuntimeError, match="interrupt"):
            run_optimization(config)
    completed = (
        config.run_dir / "evaluations/island-000-initial-000/outcome.json"
    )
    original = completed.stat().st_mtime_ns
    monkeypatch.setenv("SLURM_JOB_ID", "456")
    assert resume_optimization(config.run_dir).failed == 0
    assert completed.stat().st_mtime_ns == original
    manifest = json.loads((config.run_dir / "manifest.json").read_text())
    assert manifest["config"]["scratch_mode"] == "node_local"
    assert len(list((config.run_dir / "execution-sessions").iterdir())) == 2


def test_remote_containment_and_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path / "local"))
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "token").write_text("token")
    spec = dict(
        shared=str(shared),
        token="token",
        invocation="run",
        candidate="a",
        attempt="attempt",
        output=str(shared / "out"),
        template=str(tmp_path / "outside"),
    )
    with pytest.raises(ValueError, match="beneath"):
        bootstrap(spec)
    spec["template"] = "${TMPDIR}/scratch"
    bootstrap(spec)
    root = Path(json.loads((shared / "out").read_text())["scratch_dir"])
    (root / "log.solver").write_text("abcdefghij")
    (root / "state.json").write_text("{}")
    (root / "polyMesh").mkdir()
    (root / "polyMesh/log.hidden").write_text("mesh")
    (root / "linked.log").symlink_to(shared / "token")
    from hydroflow_opt.backends import remote

    monkeypatch.setattr(remote, "FILE_LIMIT", 5)
    output = shared / "diagnostics.tar.gz"
    collect(dict(scratch=str(root), attempt="attempt", output=str(output)))
    with tarfile.open(output) as archive:
        assert archive.extractfile("log.solver").read() == b"fghij"
        assert "linked.log" not in archive.getnames()
        assert "polyMesh/log.hidden" not in archive.getnames()
        manifest = json.load(archive.extractfile("diagnostics-manifest.json"))
        assert any(item["status"] == "tail" for item in manifest["files"])


def test_cancellation_scopes_steps_to_launcher(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *a, **k: "123.1|mine\n123.2|other\n999.1|mine\n",
    )
    assert _Attempt._owned_steps("123", "mine") == ["123.1"]


@pytest.mark.parametrize("fail", [False, True])
def test_five_stage_two_rank_probe(cluster, fail):
    script = Path(__file__).parents[1] / "examples/node_local_smoke.py"
    run_dir = cluster / "probe"
    command = [sys.executable, str(script), str(run_dir)]
    if fail:
        command.append("--fail-one")
    subprocess.run(command, check=True, timeout=60, capture_output=True)
    placements = [
        json.loads(path.read_text())
        for path in run_dir.glob("evaluations/*/placement.json")
    ]
    for placement in placements:
        path = Path(placement["scratch_dir"])
        assert path.is_relative_to(cluster.parent / placement["assigned_node"])
    if fail:
        with tarfile.open(
            run_dir / "evaluations/candidate-0/diagnostics.tar.gz"
        ) as archive:
            assert "log.probe" in archive.getnames()
            assert "state.json" in archive.getnames()
        failed = next(
            p for p in placements if "/candidate-0/" in p["scratch_dir"]
        )
        assert Path(failed["scratch_dir"]).exists()


def test_cancel_uncertain_remote_work_quarantines(cluster, monkeypatch):
    config = node_config(cluster)
    allocator = Allocator({"node-a": 1}, 1)
    from hydroflow_opt.backends.placement import PlacementConnection

    connection = PlacementConnection(("127.0.0.1", 1), b"unused", "test")
    attempt = _Attempt(
        config,
        case_from_name("quadratic"),
        connection,
        allocator,
        "node-a",
        config.candidates[0],
        cluster,
    )

    class FinishedLauncher:
        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(attempt, "_owned_steps", lambda *args: ["123.7"])
    cancelled = []
    monkeypatch.setattr(
        subprocess, "run", lambda args, **kw: cancelled.append(args)
    )
    with pytest.raises(RuntimeError, match="quarantined"):
        attempt._terminate(FinishedLauncher(), ["srun", "--job-name=mine"])
    assert cancelled == [["scancel", "--signal=KILL", "123.7"]]
    with pytest.raises(RuntimeError, match="termination"):
        allocator.acquire(os.getpid())


def test_timeout_terminates_launcher_before_forgetting_it(
    cluster, monkeypatch
):
    from hydroflow_opt.backends import node_local
    from hydroflow_opt.backends.placement import PlacementConnection
    from hydroflow_opt.models import EvaluationStage

    config = node_config(cluster)
    allocator = Allocator({"node-a": 1}, 1)
    attempt = _Attempt(
        config,
        case_from_name("quadratic"),
        PlacementConnection(("127.0.0.1", 1), b"unused", "test"),
        allocator,
        "node-a",
        config.candidates[0],
        cluster,
    )

    class Launcher:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: Launcher())
    clock = iter([0, 121])
    monkeypatch.setattr(node_local.time, "monotonic", lambda: next(clock))
    terminated = []
    monkeypatch.setattr(
        attempt, "_terminate", lambda *args: terminated.append(args)
    )
    stage = EvaluationStage(
        "remote-bootstrap",
        (*node_local._HELPER, "bootstrap", "spec"),
        cluster,
    )
    with (
        (cluster / "stdout").open("w") as stdout,
        (cluster / "stderr").open("w") as stderr,
        pytest.raises(subprocess.TimeoutExpired),
    ):
        attempt._execute_stage(
            ["srun", "--job-name=probe"],
            stage,
            {},
            stdout,
            stderr,
        )
    assert len(terminated) == 1
    assert allocator.active() == (0, [])


def test_diagnostic_failure_is_reported_separately(cluster, monkeypatch):
    from hydroflow_opt.backends.placement import PlacementConnection

    config = node_config(cluster)
    attempt = _Attempt(
        config,
        case_from_name("quadratic"),
        PlacementConnection(("127.0.0.1", 1), b"unused", "test"),
        Allocator({"node-a": 1}, 1),
        "node-a",
        config.candidates[0],
        cluster,
    )
    attempt.placement["scratch_dir"] = "/remote/scratch"

    def failed(*args):
        raise subprocess.CalledProcessError(1, "collect")

    monkeypatch.setattr(attempt, "_operation", failed)
    result = attempt._failed_result(
        config.candidates[0], attempt.paths, {}, "solver failed", "solve"
    )
    attempt.diagnostics()
    assert result.error == "solver failed"
    assert (
        json.loads((cluster / "diagnostics.json").read_text())["status"]
        == "unavailable"
    )


def test_sigterm_aborts_coordinator_and_restores_handler(cluster):
    import signal

    original = signal.getsignal(signal.SIGTERM)
    config = node_config(cluster)
    with node_local_session(config, case_from_name("quadratic")) as backend:
        with pytest.raises(SystemExit) as error:
            signal.raise_signal(signal.SIGTERM)
        assert error.value.code == 128 + signal.SIGTERM
        with pytest.raises(RuntimeError, match="signal"):
            backend.connection.connect().check()
    assert signal.getsignal(signal.SIGTERM) == original


def test_programmatic_scratch_path_survives_manifest_roundtrip(cluster):
    from hydroflow_opt.config import OptimizationConfig
    from hydroflow_opt.runner import _config_from_manifest, _effective_config

    config = replace(
        node_config(cluster),
        scratch_dir=Path("${TMPDIR}/custom"),
        scratch_dir_template=None,
        optimization=OptimizationConfig(1, 5, 1),
    )
    saved = _effective_config(config)
    assert saved["scratch_directory"]["template"] == "${TMPDIR}/custom"
    restored = _config_from_manifest(config.run_dir, saved)
    assert restored.scratch_mode == "node_local"
    assert restored.scratch_dir_template == "${TMPDIR}/custom"
    assert run_local(config).succeeded == 2
    placement = json.loads(
        (config.run_dir / "evaluations/a/placement.json").read_text()
    )
    assert "/tmp/custom/" in placement["scratch_dir"]

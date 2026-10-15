"""Small node-side helpers for scratch ownership and stage execution."""

import io
import json
import os
import shutil
import socket
import sys
import tarfile
from pathlib import Path
from string import Template
from typing import Any

FILE_LIMIT = 10 * 1024 * 1024
TOTAL_LIMIT = 100 * 1024 * 1024


def atomic_json(path: Path, value: Any) -> None:
    """Publish one JSON record atomically."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def bootstrap(spec: dict[str, Any]) -> None:
    """Resolve scratch on the target node and verify shared output access."""
    shared = Path(spec["shared"])
    if (shared / "token").read_text() != spec["token"]:
        raise RuntimeError("shared run directory token mismatch")
    tmp = os.environ.get("TMPDIR")
    if not tmp or not Path(tmp).is_absolute():
        raise ValueError("compute-node TMPDIR must be an absolute path")
    root = Path(tmp).resolve()
    template = Template(spec["template"])
    for name in template.get_identifiers():
        if not os.environ.get(name):
            raise ValueError(f"scratch template requires non-empty {name}")
    base = Path(template.substitute(os.environ))
    if not base.is_absolute() or not base.resolve().is_relative_to(root):
        raise ValueError(
            "node_local scratch must be beneath compute-node TMPDIR"
        )
    path = base / spec["invocation"] / spec["candidate"] / spec["attempt"]
    path = path.resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("scratch path escapes compute-node TMPDIR")
    path.mkdir(parents=True, exist_ok=False)
    (path / ".hydroflow-owner").write_text(spec["attempt"])
    atomic_json(
        Path(spec["output"]),
        {
            "scratch_dir": str(path),
            "hostname": socket.gethostname(),
            "node": os.environ.get("SLURMD_NODENAME", socket.gethostname()),
            "slurm_step_id": os.environ.get("SLURM_STEP_ID"),
            "attempt_id": spec["attempt"],
        },
    )


def _owned(spec: dict[str, Any]) -> Path:
    path = Path(spec["scratch"])
    if (path / ".hydroflow-owner").read_text() != spec["attempt"]:
        raise RuntimeError("scratch ownership mismatch")
    return path


def collect(spec: dict[str, Any]) -> None:
    """Export bounded diagnostics, never following filesystem symlinks."""
    root = _owned(spec)
    output = Path(spec["output"])
    temporary = output.with_name(f".{output.name}.tmp")
    records: list[dict[str, Any]] = []
    remaining = TOTAL_LIMIT
    with tarfile.open(temporary, "w:gz") as archive:
        for directory, dirs, names in os.walk(root, followlinks=False):
            parent = Path(directory)
            dirs[:] = sorted(
                name
                for name in dirs
                if not (parent / name).is_symlink()
                and not name.startswith("processor")
                and name not in {"polyMesh", "boundaryData"}
                and not name.startswith("boundaryData_")
                and not _numeric(name)
            )
            for name in sorted(names):
                path = parent / name
                relative = path.relative_to(root)
                log = name.endswith(".log") or name.startswith("log.")
                selected = (
                    log
                    or path.suffix in {".json", ".xml"}
                    or "system" in relative.parts[:-1]
                    or parent.name == "constant"
                )
                if not selected or path.is_symlink() or not path.is_file():
                    continue
                size = path.stat().st_size
                limit = min(remaining, FILE_LIMIT)
                record: dict[str, Any] = {"path": str(relative), "bytes": size}
                if size > limit and not log:
                    record["status"] = "omitted: size limit"
                elif limit <= 0:
                    record["status"] = "omitted: total size limit"
                else:
                    with path.open("rb") as stream:
                        if size > limit:
                            stream.seek(size - limit)
                        data = stream.read(limit)
                    info = tarfile.TarInfo(str(relative))
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
                    remaining -= len(data)
                    record["status"] = "tail" if size > limit else "included"
                records.append(record)
        data = json.dumps({"files": records}, indent=2).encode()
        info = tarfile.TarInfo("diagnostics-manifest.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    temporary.replace(output)


def _numeric(name: str) -> bool:
    try:
        float(name)
    except ValueError:
        return False
    return True


def execute(record_dir: Path, command: list[str]) -> None:
    """Record the actual MPI rank placement, then replace the wrapper."""
    rank = os.environ.get("SLURM_PROCID", "0")
    atomic_json(
        record_dir / f"placement-rank-{rank}.json",
        {
            "hostname": socket.gethostname(),
            "node": os.environ.get("SLURMD_NODENAME", socket.gethostname()),
            "slurm_step_id": os.environ.get("SLURM_STEP_ID"),
            "rank": rank,
        },
    )
    os.execvpe(command[0], command, os.environ)


def main() -> None:
    """Dispatch internal operations without shell evaluation."""
    operation = sys.argv[1]
    if operation == "exec":
        execute(Path(sys.argv[2]), sys.argv[3:])
        return
    spec = json.loads(Path(sys.argv[2]).read_text())
    if operation == "bootstrap":
        bootstrap(spec)
    elif operation == "collect":
        collect(spec)
    elif operation == "cleanup":
        shutil.rmtree(_owned(spec))
    else:
        raise ValueError(f"unknown remote operation: {operation}")


if __name__ == "__main__":
    main()

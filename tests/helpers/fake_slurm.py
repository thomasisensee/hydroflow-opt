"""Process-level Slurm substitute with distinct node-local directories."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

name = Path(sys.argv[0]).name
if name == "scontrol":
    print("node-a\nnode-b")
    sys.exit(0)
if name in {"squeue", "scancel"}:
    sys.exit(0)
args = sys.argv[1:]
options = {}
while args and args[0].startswith("--"):
    key, _, value = args.pop(0).partition("=")
    options[key] = value
node = options["--nodelist"]
root = Path(os.environ["FAKE_SLURM_ROOT"])
tmp = root / node / "tmp"
tmp.mkdir(parents=True, exist_ok=True)
env = dict(
    os.environ,
    TMPDIR=str(tmp),
    SLURMD_NODENAME=node,
    SLURM_STEP_ID=uuid.uuid4().hex,
)
# The controller must always launch from persistent shared storage.
assert Path.cwd().is_relative_to(root / "shared")
if os.environ.get("FAIL_BOOTSTRAP") and "bootstrap" in args:
    sys.exit(19)
records = root / "launches"
records.mkdir(exist_ok=True)
(records / f"{uuid.uuid4().hex}.json").write_text(
    json.dumps(
        {
            "node": node,
            "cwd": options["--chdir"],
            "command": args,
        }
    )
)
workers = [
    subprocess.Popen(
        args,
        cwd=options["--chdir"],
        env=dict(env, SLURM_PROCID=str(rank)),
    )
    for rank in range(int(options["--ntasks"]))
]
sys.exit(max(p.wait() for p in workers))

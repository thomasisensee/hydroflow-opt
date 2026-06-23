"""Subprocess entry point for the built-in quadratic case."""

import json
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Evaluate a request written by :class:`SubprocessBackend`."""

    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        raise SystemExit("usage: python -m flow_opt.toy_worker REQUEST RESULT")
    request_path, result_path = (Path(value) for value in args)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    start = time.perf_counter()
    objective = sum(
        float(value) ** 2
        for value in request["candidate"]["parameters"].values()
    )
    result = {
        "candidate_id": request["candidate"]["id"],
        "status": "success",
        "objective": objective,
        "timings": {"evaluation": time.perf_counter() - start},
        "metadata": {},
        "error": None,
    }
    result_path.write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

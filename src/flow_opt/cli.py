"""Command-line interface for portable runs and pygmo optimization."""

import argparse
from pathlib import Path

from flow_opt.cases import case_from_name
from flow_opt.config import load_config
from flow_opt.runner import inspect_run, run_local, run_optimization


def main(argv: list[str] | None = None) -> int:
    """Run the ``flow-opt`` command line interface."""

    parser = argparse.ArgumentParser(prog="flow-opt")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "run", "optimize"):
        child = subparsers.add_parser(command)
        child.add_argument("config", type=Path)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)

    if args.command == "inspect":
        summary = inspect_run(args.run_dir)
        print(
            f"{summary.succeeded}/{summary.total} succeeded, "
            f"{summary.failed} failed"
        )
        return 0

    config = load_config(args.config)
    case_from_name(config.case_name)
    if args.command == "check":
        print(f"configuration ok: case={config.case_name}")
        return 0
    summary = (
        run_local(config, config_path=args.config)
        if args.command == "run"
        else run_optimization(config, config_path=args.config)
    )
    print(
        f"run complete: {summary.succeeded}/{summary.total} succeeded, "
        f"results={summary.results_path}"
    )
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

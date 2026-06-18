"""Command-line interface for flow-opt."""

import argparse
from pathlib import Path

from flow_opt.config import load_config
from flow_opt.evaluators import evaluator_from_name
from flow_opt.runner import inspect_run, run_local


def main(argv: list[str] | None = None) -> int:
    """Run the flow-opt command-line interface.

    Parameters
    ----------
    argv
        Optional argument list. If ``None``, arguments are read from
        ``sys.argv`` by ``argparse``. Passing a list is mainly useful for
        tests.

    Returns
    -------
    int
        Process-style exit code. ``0`` indicates success. The ``run`` command
        returns ``1`` if any candidate evaluation failed.

    Raises
    ------
    SystemExit
        Raised by ``argparse`` for invalid command-line arguments.
    ValueError
        Raised when configuration validation or evaluator lookup fails.
    """

    parser = argparse.ArgumentParser(prog="flow-opt")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("config", type=Path)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("config", type=Path)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("run_dir", type=Path)

    args = parser.parse_args(argv)
    match args.command:
        case "check":
            config = load_config(args.config)
            evaluator_from_name(config.evaluator)
            print(
                "configuration ok: "
                f"{len(config.candidates)} candidate(s), "
                f"evaluator={config.evaluator}"
            )
            return 0
        case "run":
            config = load_config(args.config)
            summary = run_local(config, config_path=args.config)
            print(
                "run complete: "
                f"{summary.succeeded}/{summary.total} succeeded, "
                f"results={summary.results_path}"
            )
            return 0 if summary.failed == 0 else 1
        case "inspect":
            summary = inspect_run(args.run_dir)
            print(
                f"{summary.succeeded}/{summary.total} succeeded, "
                f"{summary.failed} failed"
            )
            return 0
        case _:
            parser.error(f"unknown command: {args.command}")
            return 2


if __name__ == "__main__":
    raise SystemExit(main())

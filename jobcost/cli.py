"""Command line interface. Run `python -m jobcost --help`."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .config import settings_from_env
from .costing import analyze, job_to_dict, portfolio_to_dict
from .importer import DataError, load_dataset, parse_date
from .render_html import render_html
from .render_text import render_flags, render_issues, render_job, render_summary, render_trades
from .sample import write_sample

EXIT_OK, EXIT_ERROR, EXIT_FLAGGED = 0, 1, 2


def _target(text: str) -> float:
    """Accept 35, 35% or 0.35 on the command line."""
    value = float(text.strip().rstrip("%"))
    if value >= 1:
        value /= 100
    if not 0 < value < 1:
        raise argparse.ArgumentTypeError("target must be between 0 and 100 percent")
    return value


def _as_of(text: str):
    try:
        return parse_date(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", default="data/sample", help="folder with jobs.csv, estimates.csv, actuals.csv")
    common.add_argument("--target", type=_target, help="default target margin, for example 35")
    common.add_argument("--as-of", type=_as_of, help="ignore actuals and progress after this date (YYYY-MM-DD)")
    common.add_argument("--strict", action="store_true", help="fail if any CSV row is invalid")

    parser = argparse.ArgumentParser(
        prog="jobcost",
        description="Job costing: estimate versus actual by trade, margin drift and target flags.",
    )
    parser.add_argument("--version", action="version", version=f"jobcost {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("summary", parents=[common], help="one line per job with margin and drift")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")

    sub.add_parser("trades", parents=[common], help="estimate versus forecast by trade across all jobs")

    p = sub.add_parser("job", parents=[common], help="trade-level detail for one job")
    p.add_argument("job_id")
    p.add_argument("--json", action="store_true", help="print JSON instead of tables")

    p = sub.add_parser("flags", parents=[common], help="jobs below or trending below target margin")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p.add_argument("--fail-on-flag", action="store_true", help="exit with code 2 when any job is flagged")

    p = sub.add_parser("report", parents=[common], help="write the static HTML report")
    p.add_argument("--out", default="report.html", help="output file (default report.html)")
    p.add_argument("--title", default="Job costing report")

    sub.add_parser("validate", parents=[common], help="check the CSV files and list rows that would be skipped")

    p = sub.add_parser("sample", help="write a synthetic data set")
    p.add_argument("--out", default="data/sample", help="output folder (default data/sample)")
    p.add_argument("--seed", type=int, default=7)
    return parser


def _warn_issues(data, out) -> None:
    if data.issues:
        print(f"warning: {len(data.issues)} CSV row(s) skipped; run `jobcost validate` for details", file=out)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "sample":
        counts = write_sample(args.out, args.seed)
        print(f"Wrote synthetic data to {Path(args.out).as_posix()}")
        for name, rows in counts.items():
            print(f"  {name}: {rows} rows")
        return EXIT_OK

    try:
        settings = settings_from_env()
        if args.target is not None:
            settings = replace(settings, target_margin=args.target).validate()
        data = load_dataset(args.data, strict=args.strict)
    except DataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        for issue in exc.issues:
            print(f"  {issue}", file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.command == "validate":
        print(f"{Path(args.data).as_posix()}: {len(data.jobs)} jobs, {len(data.estimates)} estimate lines, "
              f"{len(data.actuals)} actual entries, {len(data.progress)} progress reports")
        print(render_issues(data.issues))
        return EXIT_ERROR if data.issues else EXIT_OK

    portfolio = analyze(data, settings, args.as_of)

    if args.command == "summary":
        if args.json:
            print(json.dumps(portfolio_to_dict(portfolio), indent=2))
        else:
            print(render_summary(portfolio))
            _warn_issues(data, sys.stderr)
    elif args.command == "trades":
        print(render_trades(portfolio))
        _warn_issues(data, sys.stderr)
    elif args.command == "job":
        match = [j for j in portfolio.jobs if j.job.job_id.lower() == args.job_id.lower()]
        if not match:
            known = ", ".join(sorted(data.jobs))
            print(f"error: no job {args.job_id!r}. Known jobs: {known}", file=sys.stderr)
            return EXIT_ERROR
        print(json.dumps(job_to_dict(match[0]), indent=2) if args.json else render_job(match[0]))
    elif args.command == "flags":
        if args.json:
            print(json.dumps([job_to_dict(j, detail=False) for j in portfolio.flagged], indent=2))
        else:
            print(render_flags(portfolio))
        if args.fail_on_flag and portfolio.flagged:
            return EXIT_FLAGGED
    elif args.command == "report":
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(portfolio, data.issues, args.title), encoding="utf-8", newline="\n")
        flagged = len(portfolio.flagged)
        print(f"Wrote {out.as_posix()} ({len(portfolio.jobs)} jobs, {flagged} flagged)")
        _warn_issues(data, sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

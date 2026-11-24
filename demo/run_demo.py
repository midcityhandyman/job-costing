"""Offline demo: runs every CLI view against the synthetic data and saves the transcript.

    python demo/run_demo.py

Writes demo/output.txt and demo/report.html. Needs no network, keys or installs.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jobcost.cli import main  # noqa: E402

STEPS = [
    ("Check the synthetic data set imports cleanly", ["validate", "--data", "data/sample"]),
    ("Portfolio summary: margin, drift and status per job", ["summary", "--data", "data/sample"]),
    ("Which jobs are below, or trending below, target margin", ["flags", "--data", "data/sample"]),
    ("Estimate versus forecast by trade across all jobs", ["trades", "--data", "data/sample"]),
    ("Drill into the job that is trending below target", ["job", "J-103", "--data", "data/sample"]),
    ("Same data three weeks earlier: J-103 was only on watch and J-102 still looked fine",
     ["summary", "--data", "data/sample", "--as-of", "2026-08-21"]),
    ("Use as a gate in a scheduled job: exit code 2 when anything is flagged",
     ["flags", "--data", "data/sample", "--fail-on-flag", "--json"]),
    ("A messy export: bad rows are skipped and listed, good rows still load",
     ["validate", "--data", "demo/messy"]),
    ("Write the static HTML report", ["report", "--data", "data/sample", "--out", "demo/report.html"]),
]


def run() -> str:
    os.chdir(ROOT)
    lines = ["job-costing demo (synthetic data, fully offline)", ""]
    for title, argv in STEPS:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(argv)
        lines.append("=" * 100)
        lines.append(f"# {title}")
        lines.append(f"$ python -m jobcost {' '.join(argv)}")
        lines.append("")
        lines.append(buffer.getvalue().rstrip())
        lines.append(f"[exit code {code}]")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    transcript = run()
    (ROOT / "demo" / "output.txt").write_text(transcript, encoding="utf-8", newline="\n")
    print(transcript)

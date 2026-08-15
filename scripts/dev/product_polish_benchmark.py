#!/usr/bin/env python3
"""Local, zero-cost performance observations for Manju's core product surfaces.

This is a *measurement* tool, not a hidden cache and not a build input.  It
creates disposable 12/100/300-shot projects with tiny local PNG takes, warms the
read paths, records repeated wall-clock samples, and emits deterministic JSON
metadata around the observations.

By default the script never fails on timing: developer laptops and CI runners
vary too much for an absolute wall-clock assertion.  ``--enforce-cockpit`` is an
explicit release-candidate option that accepts per-size budgets such as
``12=150,100=600,300=1400`` and fails only the Cockpit medians that exceed them.
No provider, credential, network request, FFmpeg process, or paid service is
used.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_sha(root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _parse_sizes(raw: str) -> list[int]:
    values: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        value = int(token)
        if value <= 0:
            raise argparse.ArgumentTypeError("shot sizes must be positive")
        values.append(value)
    if not values:
        raise argparse.ArgumentTypeError("at least one shot size is required")
    return values


def _parse_budgets(raw: str | None) -> dict[int, float]:
    if not raw:
        return {}
    out: dict[int, float] = {}
    for token in raw.split(","):
        size_s, sep, budget_s = token.strip().partition("=")
        if not sep:
            raise argparse.ArgumentTypeError(
                "budgets must look like 12=150,100=600,300=1400"
            )
        size, budget = int(size_s), float(budget_s)
        if size <= 0 or budget <= 0:
            raise argparse.ArgumentTypeError("budget sizes and milliseconds must be positive")
        out[size] = budget
    return out


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, int((percentile / 100.0) * len(ordered) + 0.999999))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _measure(fn: Callable[[], Any], *, warmups: int, samples: int) -> dict[str, Any]:
    result: Any = None
    for _ in range(warmups):
        result = fn()
    timings: list[float] = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        result = fn()
        timings.append((time.perf_counter_ns() - started) / 1_000_000.0)
    return {
        "median_ms": round(statistics.median(timings), 3),
        "p95_ms": round(_percentile(timings, 95), 3),
        "min_ms": round(min(timings), 3),
        "max_ms": round(max(timings), 3),
        "samples_ms": [round(v, 3) for v in timings],
        "result_size": len(result) if isinstance(result, (str, bytes, list, dict)) else None,
    }


class _EmptyRunner:
    def list(self) -> list[Any]:
        return []

    def interrupted(self) -> list[Any]:
        return []


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = _repo_root()
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(root))

    # Imports are intentionally delayed until the source tree is on sys.path.
    from tests.fixtures.make_gui_scale_project import build as build_scale_project
    from manju.core.container import Project
    from manju.gui.cockpit import cockpit_data
    from manju.gui.pages import render_review
    from manju.gui.state import build_state
    from manju.gui.storyboard import render as render_storyboard

    os.environ["MANJU_EXECUTION_MODE"] = "strict_zero_cost"

    owned_workdir = args.work_dir is None
    work_dir = Path(args.work_dir).resolve() if args.work_dir else Path(
        tempfile.mkdtemp(prefix="manju-product-benchmark-")
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MANJU_GUI_STATE"] = str(work_dir / "gui-state.json")

    payload: dict[str, Any] = {
        "schema": "manju.product-polish-performance/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git_sha(root),
        "zero_cost": True,
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
        },
        "config": {
            "sizes": args.sizes,
            "takes_per_shot": args.takes_per_shot,
            "warmups": args.warmups,
            "samples": args.samples,
        },
        "results": {},
    }

    try:
        for shots in args.sizes:
            project_root = build_scale_project(
                work_dir / f"shots-{shots}", shots=shots,
                takes_per_shot=args.takes_per_shot,
                name=f"scale_{shots}",
            )
            project = Project(project_root)
            token = f"benchmark-{shots}"
            payload["results"][str(shots)] = {
                "build_state": _measure(
                    lambda: build_state(project, _EmptyRunner()),
                    warmups=args.warmups, samples=args.samples,
                ),
                "cockpit": _measure(
                    lambda: cockpit_data(project),
                    warmups=args.warmups, samples=args.samples,
                ),
                "review_html": _measure(
                    lambda: render_review(project, token),
                    warmups=args.warmups, samples=args.samples,
                ),
                "storyboard_html": _measure(
                    lambda: render_storyboard(project, token),
                    warmups=args.warmups, samples=args.samples,
                ),
            }
    finally:
        if owned_workdir and not args.keep_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)

    budgets = args.enforce_cockpit
    failures: list[dict[str, Any]] = []
    for shots, budget in budgets.items():
        row = payload["results"].get(str(shots))
        if row is None:
            failures.append({"shots": shots, "budget_ms": budget, "reason": "size not measured"})
            continue
        actual = float(row["cockpit"]["median_ms"])
        if actual > budget:
            failures.append({"shots": shots, "budget_ms": budget, "actual_ms": actual})
    payload["enforcement"] = {
        "enabled": bool(budgets),
        "cockpit_median_budgets_ms": {str(k): v for k, v in budgets.items()},
        "failures": failures,
        "passed": not failures,
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=_parse_sizes, default=_parse_sizes("12,100,300"))
    parser.add_argument("--takes-per-shot", type=int, default=1)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--keep-work-dir", action="store_true")
    parser.add_argument(
        "--enforce-cockpit", type=_parse_budgets, default={}, metavar="SIZE=MS,...",
        help="optional median budgets, e.g. 12=150,100=600,300=1400",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.takes_per_shot <= 0 or args.warmups < 0 or args.samples <= 0:
        parser.error("takes-per-shot and samples must be positive; warmups may be zero")

    payload = run(args)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if payload["enforcement"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Stamp REPORTS/LAST_GREEN.yaml from CI results (dev/CI harness, NOT a test).

INFORMATIONAL ONLY. This writes a DERIVED status record and imports nothing
from ``src/`` — LAST_GREEN.yaml is never a build input, never runtime truth.
Intended to run in a CI job that fires only after BOTH the Ubuntu suite
(ci.yml) and the Windows suite (windows-ci.yml) go green on the SAME commit.

Usage (values normally come from the CI context / GITHUB_* env):

    python scripts/dev/update_last_green.py \
        --commit "$GITHUB_SHA" \
        --ubuntu-run 123 --ubuntu-tests 4700 \
        --windows-run 456 --windows-tests 4700 \
        --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

Omitted fields stay null/pending, so a partial run never claims success.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

REPORT = Path(__file__).resolve().parents[2] / "REPORTS" / "LAST_GREEN.yaml"


def build_record(args: argparse.Namespace) -> dict:
    def result(run: object) -> str:
        return "success" if run is not None else "pending"

    return {
        "commit": args.commit,
        "ubuntu": {
            "workflow_run": args.ubuntu_run,
            "result": result(args.ubuntu_run),
            "test_count": args.ubuntu_tests,
        },
        "windows": {
            "workflow_run": args.windows_run,
            "result": result(args.windows_run),
            "test_count": args.windows_tests,
        },
        # A green Windows run IS the ffmpeg evidence: windows-ci.yml installs the
        # pinned build and then asserts the version ("FFmpeg present and at the
        # pinned version (anti-silent-skip gate)"), so the job cannot go green on
        # a moved binary or a silently-skipped ffmpeg suite. No separate probe.
        "ffmpeg": {"result": "success" if args.windows_run is not None else "pending"},
        "generated_at": args.generated_at,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--commit")
    p.add_argument("--ubuntu-run")
    p.add_argument("--ubuntu-tests", type=int)
    p.add_argument("--windows-run")
    p.add_argument("--windows-tests", type=int)
    p.add_argument("--generated-at")
    args = p.parse_args()

    header = (
        "# REPORTS/LAST_GREEN.yaml — the latest fully validated commit.\n"
        "# INFORMATIONAL ONLY: derived, never a build input, never runtime truth.\n"
        "# Written by scripts/dev/update_last_green.py from CI results.\n"
        "#\n"
        "# WHO RUNS IT: a maintainer, BY HAND, after reading both runs. No\n"
        "# workflow calls this script — the earlier 'stamped by CI' wording was\n"
        "# aspirational and left the file at `pending` forever (fixed 2026-08-01,\n"
        "# DECISIONS `DOCS-CLOSEOUT #3`; tests/test_docs_closeout.py keeps the\n"
        "# claim and the workflows in sync in both directions).\n"
        "# Values are MEASURED, never typed from memory: `result: success` may\n"
        "# only appear next to a real workflow_run id, and a count nobody read\n"
        "# stays null rather than becoming a plausible number.\n\n"
    )
    body = yaml.safe_dump(build_record(args), allow_unicode=True, sort_keys=False)
    REPORT.write_text(header + body, encoding="utf-8")
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    main()

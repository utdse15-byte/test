#!/usr/bin/env python3
"""Run Manju's bounded, zero-cost local release-candidate gate.

The tool composes existing focused tests and deterministic acceptance aids into
one auditable run.  It never calls a real Provider, never reads cloud
credentials, never downloads packages or browsers, and never updates
``LAST_GREEN``.  Windows and Ubuntu same-SHA CI remain separate hard gates.

Examples::

    python scripts/dev/product_release_candidate.py \
      --output REPORTS/product-polish-r1/local-rc

    python scripts/dev/product_release_candidate.py \
      --output /tmp/manju-rc --profile quick

    python scripts/dev/product_release_candidate.py \
      --output /tmp/manju-rc --dry-run
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time
from typing import Any


@dataclass(frozen=True)
class Stage:
    name: str
    command: tuple[str, ...]
    timeout_s: int
    required: bool = True
    profiles: tuple[str, ...] = ("quick", "standard", "full")
    availability: str | None = None


_SENSITIVE_PARTS = (
    "API_KEY", "ACCESS_KEY", "SECRET", "PASSWORD", "PASSWD", "TOKEN",
    "CREDENTIAL", "PRIVATE_KEY", "CLIENT_SECRET", "SESSION_COOKIE",
)
_SENSITIVE_PREFIXES = (
    "AWS_", "AZURE_", "GOOGLE_", "OPENAI_", "ANTHROPIC_", "DASHSCOPE_",
    "SERPER_", "HF_", "HUGGINGFACE_", "GITHUB_TOKEN", "NPM_TOKEN",
)


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, encoding="utf-8",
            errors="replace", stderr=subprocess.DEVNULL, timeout=10,
        ).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _sensitive_name(name: str) -> bool:
    upper = name.upper()
    return any(part in upper for part in _SENSITIVE_PARTS) or any(
        upper.startswith(prefix) for prefix in _SENSITIVE_PREFIXES
    )


def _sanitized_env(base: dict[str, str] | None = None, *, work: Path | None = None) -> dict[str, str]:
    """Return a child environment that cannot inherit cloud credentials.

    This is an additional development-gate defense.  Product code still owns
    the real strict-zero-cost credential/transport boundary.
    """

    source = dict(os.environ if base is None else base)
    env = {key: value for key, value in source.items() if not _sensitive_name(key)}
    env.update({
        "MANJU_EXECUTION_MODE": "strict_zero_cost",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "NO_PROXY": "localhost,127.0.0.1,::1",
        "no_proxy": "localhost,127.0.0.1,::1",
    })
    if work is not None:
        env["MANJU_GUI_STATE"] = str(work / "gui-state.json")
        env["MANJU_RECENTS"] = str(work / "recents.json")
    root = _root()
    existing = env.get("PYTHONPATH", "")
    parts = [str(root / "src"), str(root)]
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _stages(root: Path, output: Path, chromium: str, *, require_ruff: bool) -> list[Stage]:
    py = sys.executable
    isolated_product_tests = {
        "test_product_polish_command_palette.py",
        "test_product_polish_dev_gates.py",
        "test_product_polish_help_center.py",
    }
    product_tests = tuple(
        str(path.relative_to(root))
        for path in sorted(root.glob("tests/test_product_polish*.py"))
        if path.name not in isolated_product_tests
    )
    trust_tests = (
        "tests/test_zero_cost_provider_policy.py",
        "tests/test_roundtrip_baseline_absent.py",
        "tests/test_roundtrip_needs_baseline.py",
        "tests/test_roundtrip_fcpxml.py",
        "tests/test_gui_project_actions.py",
        "tests/test_windows_install.py",
    )
    stages = [
        Stage(
            "compile",
            (py, "-m", "compileall", "-q", "src", "tests", "scripts/dev"),
            180,
        ),
        Stage(
            "ruff",
            (shutil.which("ruff") or "ruff", "check", "src", "tests", "scripts/dev"),
            180,
            required=require_ruff,
            availability="available" if _tool_available("ruff") else "unavailable",
        ),
        Stage(
            "product-polish-core-tests",
            (py, "-m", "pytest", "-q", *product_tests),
            900,
        ),
        # Playwright-backed files are separate processes.  Combining several
        # browser/server suites into one long pytest invocation can leave a
        # third-party browser child draining after pytest has printed its
        # summary; isolation makes the release log honest and bounded.
        Stage(
            "quick-open-tests",
            (py, "-m", "pytest", "-q", "tests/test_product_polish_command_palette.py"),
            300,
        ),
        Stage(
            "help-center-tests",
            (py, "-m", "pytest", "-q", "tests/test_product_polish_help_center.py"),
            300,
        ),
        Stage(
            "trust-boundary-tests",
            (py, "-m", "pytest", "-q", *trust_tests),
            900,
            profiles=("standard", "full"),
        ),
        Stage(
            "performance-gate",
            (
                py, "scripts/dev/product_polish_benchmark.py",
                "--enforce-cockpit", "12=150,100=600,300=1400",
                "--output", str(output / "performance.json"),
            ),
            300,
            profiles=("standard", "full"),
        ),
        Stage(
            "visual-acceptance",
            (
                py, "scripts/dev/product_visual_acceptance.py",
                "--output", str(output / "visual"),
                "--chromium", chromium,
            ),
            300,
            profiles=("standard", "full"),
            availability="available" if Path(chromium).is_file() and _module_available("playwright") else "unavailable",
        ),
        Stage(
            "quick-open-acceptance",
            (
                py, "scripts/dev/product_command_palette_acceptance.py",
                "--output", str(output / "quick-open"),
                "--chromium", chromium,
            ),
            180,
            profiles=("standard", "full"),
            availability="available" if Path(chromium).is_file() and _module_available("playwright") else "unavailable",
        ),
        Stage(
            "help-center-acceptance",
            (
                py, "scripts/dev/product_help_center_acceptance.py",
                "--output", str(output / "help-center"),
                "--chromium", chromium,
            ),
            180,
            profiles=("standard", "full"),
            availability="available" if Path(chromium).is_file() and _module_available("playwright") else "unavailable",
        ),
        Stage(
            "windows-app-self-test",
            (py, "-m", "manju.gui.windows_app", "--self-test"),
            120,
        ),
        Stage(
            "wheel-build",
            (
                py, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation",
                "--wheel-dir", str(output / "wheel"),
            ),
            300,
            profiles=("standard", "full"),
        ),
    ]
    return stages


def _terminate(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except Exception:
            pass


def _run_stage(
    stage: Stage,
    *,
    root: Path,
    output: Path,
    env: dict[str, str],
    dry_run: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": stage.name,
        "required": stage.required,
        "command": list(stage.command),
        "timeout_s": stage.timeout_s,
        "availability": stage.availability or "available",
    }
    if stage.availability == "unavailable":
        row["status"] = "failed" if stage.required else "unavailable"
        row["exit_code"] = None
        return row
    if dry_run:
        row["status"] = "planned"
        row["exit_code"] = None
        return row

    logs = output / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / f"{stage.name}.stdout.log"
    stderr_path = logs / f"{stage.name}.stderr.log"
    row["stdout"] = stdout_path.relative_to(output).as_posix()
    row["stderr"] = stderr_path.relative_to(output).as_posix()
    started = time.perf_counter()
    with stdout_path.open("w", encoding="utf-8", errors="replace") as stdout, stderr_path.open(
        "w", encoding="utf-8", errors="replace"
    ) as stderr:
        process = subprocess.Popen(
            list(stage.command),
            cwd=root,
            env=env,
            stdout=stdout,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=(os.name != "nt"),
        )
        try:
            exit_code = process.wait(timeout=stage.timeout_s)
            row["status"] = "passed" if exit_code == 0 else "failed"
            row["exit_code"] = exit_code
        except subprocess.TimeoutExpired:
            _terminate(process)
            row["status"] = "timeout"
            row["exit_code"] = process.poll()
    row["duration_s"] = round(time.perf_counter() - started, 3)
    return row


def _wheel_install_smoke(root: Path, output: Path, env: dict[str, str]) -> dict[str, Any]:
    """Install the freshly built wheel into an isolated target and self-test it."""

    row: dict[str, Any] = {
        "name": "wheel-install-smoke",
        "required": True,
        "availability": "available",
    }
    wheels = sorted((output / "wheel").glob("manju-*.whl"))
    if len(wheels) != 1:
        row.update(status="failed", exit_code=None, error=f"expected one wheel, found {len(wheels)}")
        return row
    target = output / "wheel-install"
    target.mkdir(parents=True, exist_ok=True)
    logs = output / "logs"
    stdout_path = logs / "wheel-install-smoke.stdout.log"
    stderr_path = logs / "wheel-install-smoke.stderr.log"
    row["wheel"] = wheels[0].relative_to(output).as_posix()
    row["stdout"] = stdout_path.relative_to(output).as_posix()
    row["stderr"] = stderr_path.relative_to(output).as_posix()
    install = [
        sys.executable, "-m", "pip", "install", "--no-deps", "--no-index",
        "--target", str(target), str(wheels[0]),
    ]
    smoke = [
        sys.executable, "-c",
        "import manju; from manju.gui.brand import app_icon_ico_bytes; "
        "assert app_icon_ico_bytes()[:4] == b'\\x00\\x00\\x01\\x00'; "
        "from manju.gui.windows_app import self_test; raise SystemExit(self_test())",
    ]
    started = time.perf_counter()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        install_result = subprocess.run(
            install, cwd=root, env=env, stdout=stdout, stderr=stderr,
            text=True, encoding="utf-8", errors="replace", timeout=180, check=False,
        )
        if install_result.returncode != 0:
            row.update(status="failed", exit_code=install_result.returncode)
        else:
            smoke_env = dict(env)
            smoke_env["PYTHONPATH"] = str(target)
            smoke_result = subprocess.run(
                smoke, cwd=output, env=smoke_env, stdout=stdout, stderr=stderr,
                text=True, encoding="utf-8", errors="replace", timeout=120, check=False,
            )
            row.update(
                status="passed" if smoke_result.returncode == 0 else "failed",
                exit_code=smoke_result.returncode,
            )
    row["duration_s"] = round(time.perf_counter() - started, 3)
    return row


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = _root()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = output / "work"
    work.mkdir(parents=True, exist_ok=True)
    env = _sanitized_env(work=work)
    stages = [stage for stage in _stages(
        root, output, args.chromium, require_ruff=args.require_ruff
    ) if args.profile in stage.profiles]

    result: dict[str, Any] = {
        "schema": "manju.product-release-candidate/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": args.profile,
        "dry_run": args.dry_run,
        "zero_cost": True,
        "claim": "local-release-candidate-only",
        "git": {
            "branch": _git(root, "branch", "--show-current"),
            "head": _git(root, "rev-parse", "HEAD"),
            "tree": _git(root, "rev-parse", "HEAD^{tree}"),
            "dirty": bool(_git(root, "status", "--porcelain")),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "chromium": args.chromium if Path(args.chromium).is_file() else None,
            "playwright": _module_available("playwright"),
            "ruff": _tool_available("ruff"),
            "powershell": _tool_available("pwsh") or _tool_available("powershell"),
        },
        "credential_environment_removed": True,
        "stages": [],
        "hard_gates_not_claimed": [
            "full repository pytest suite",
            "live localhost browser E2E",
            "real Windows 11 App-mode Dogfood",
            "Windows hard release gate",
            "same-SHA Ubuntu and Windows green",
            "real Provider, billing or AI-video quality",
        ],
    }

    for stage in stages:
        row = _run_stage(stage, root=root, output=output, env=env, dry_run=args.dry_run)
        result["stages"].append(row)
        if not args.dry_run and stage.name == "wheel-build" and row["status"] == "passed":
            result["stages"].append(_wheel_install_smoke(root, output, env))
        if args.fail_fast and row["status"] in {"failed", "timeout"} and row["required"]:
            break

    required_failures = [
        row for row in result["stages"]
        if row.get("required") and row.get("status") not in {"passed", "planned"}
    ]
    result["passed"] = not required_failures
    result["required_failures"] = [row["name"] for row in required_failures]

    result_path = output / "release-candidate.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = [
        "# Manju local release-candidate gate",
        "",
        f"- Profile: `{args.profile}`",
        f"- Zero-cost: `{str(result['zero_cost']).lower()}`",
        f"- Result: `{'PASS' if result['passed'] else 'FAIL'}`",
        f"- HEAD: `{result['git']['head'] or 'unknown'}`",
        "",
        "| Stage | Required | Status | Duration |",
        "|---|---:|---|---:|",
    ]
    for row in result["stages"]:
        summary.append(
            f"| `{row['name']}` | {'yes' if row.get('required') else 'no'} | "
            f"{row.get('status')} | {row.get('duration_s', '')} |"
        )
    summary.extend([
        "",
        "This is a local, zero-cost candidate gate. It does not replace the same-SHA Windows/Ubuntu release gates.",
    ])
    (output / "release-candidate.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("quick", "standard", "full"), default="standard")
    parser.add_argument("--chromium", default=os.environ.get("MANJU_DEV_CHROMIUM", "/usr/bin/chromium"))
    parser.add_argument("--require-ruff", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

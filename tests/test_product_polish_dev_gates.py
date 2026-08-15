"""Wave 12 — the local performance and visual gates stay runnable and bounded."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "scripts" / "dev" / "product_polish_benchmark.py"
VISUAL = ROOT / "scripts" / "dev" / "product_visual_acceptance.py"


def test_product_benchmark_smoke_is_zero_cost_and_enforceable(tmp_path):
    output = tmp_path / "benchmark.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(BENCHMARK),
            "--sizes", "1",
            "--warmups", "0",
            "--samples", "1",
            "--takes-per-shot", "1",
            "--enforce-cockpit", "1=5000",
            "--output", str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == "manju.product-polish-performance/v1"
    assert payload["zero_cost"] is True
    assert payload["config"]["sizes"] == [1]
    assert payload["enforcement"]["passed"] is True
    assert set(payload["results"]["1"]) == {
        "build_state", "cockpit", "review_html", "storyboard_html"
    }


@pytest.mark.skipif(not Path("/usr/bin/chromium").exists(), reason="Chromium unavailable")
def test_visual_acceptance_smoke_uses_real_renderer_without_http(tmp_path):
    output = tmp_path / "visual"
    completed = subprocess.run(
        [
            sys.executable,
            str(VISUAL),
            "--output", str(output),
            "--pages", "workspace",
            "--viewports", "narrow",
            "--no-screenshots",
            "--chromium", "/usr/bin/chromium",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads((output / "visual-acceptance.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "manju.product-visual-acceptance/v1"
    assert payload["zero_cost"] is True
    assert payload["live_http_e2e"] is False
    assert payload["config"] == {
        "pages": ["workspace"],
        "viewports": ["narrow"],
        "screenshots": False,
    }
    assert payload["passed"] is True
    facts = payload["pages"]["workspace"]["narrow"]
    assert facts["horizontalOverflow"] == 0
    assert facts["lang"] == "zh-CN"
    assert "screenshot" not in facts

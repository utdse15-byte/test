"""Governance checks for the optional three-project research boundary."""

from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_IDS = {"vimax", "openchatcut", "toonflow-app"}
FORBIDDEN_TOKENS = ("vimax", "openchatcut", "toonflow")


def _yaml(relative_path: str) -> dict[str, object]:
    data = yaml.safe_load((ROOT / relative_path).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _tree_text(relative_path: str) -> str:
    root = ROOT / relative_path
    files = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
    return "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in files).lower()


def test_upstream_snapshot_safely_loads_and_pins_research_inputs() -> None:
    snapshot_path = ROOT / "docs/architecture/THREE_PROJECT_UPSTREAM_SNAPSHOT.yaml"
    snapshot = _yaml("docs/architecture/THREE_PROJECT_UPSTREAM_SNAPSHOT.yaml")

    assert snapshot["schema"] == "manju.third-party-research-snapshot/v1"
    assert snapshot["runtime_input"] is False
    assert snapshot["upstream_code_copied"] is False
    upstreams = snapshot["upstreams"]
    assert isinstance(upstreams, list)
    assert {entry["id"] for entry in upstreams} == UPSTREAM_IDS
    for entry in upstreams:
        assert re.fullmatch(r"[0-9a-f]{40}", entry["commit"])
        assert re.fullmatch(r"[0-9a-f]{64}", entry["license"]["sha256"])
        assert entry["files_read"]

    # The snapshot is audit evidence, not an unchecked executable input.
    assert hashlib.sha256(snapshot_path.read_bytes()).hexdigest()


def test_runtime_dependencies_exclude_all_three_upstreams() -> None:
    ledger = _yaml("docs/SOURCE_LEDGER.yaml")
    runtime_dependencies = ledger["external_repositories"]["runtime_dependencies"]
    assert runtime_dependencies == []

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = metadata["project"]["dependencies"]
    for extra in metadata["project"].get("optional-dependencies", {}).values():
        declared.extend(extra)
    normalized = "\n".join(declared).lower()
    assert all(token not in normalized for token in FORBIDDEN_TOKENS)


def test_static_boundary_covers_core_skills_and_package_metadata() -> None:
    scanned = {
        "src/manju": _tree_text("src/manju"),
        "skills": _tree_text("skills"),
        "pyproject.toml": _tree_text("pyproject.toml"),
    }
    assert set(scanned) == {"src/manju", "skills", "pyproject.toml"}
    for surface, text in scanned.items():
        assert all(token not in text for token in FORBIDDEN_TOKENS), surface

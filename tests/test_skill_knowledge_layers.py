"""Behavioral governance for the S1 soft-capability layer."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from manju.core.authoring import SceneContract
from manju.core.models import ShotSpec


ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "skills" / "evals" / "cases"


def test_ten_minimum_director_evals_are_structured_and_parseable() -> None:
    paths = sorted(CASES.glob("*.yaml"))
    assert len(paths) == 10
    rows = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in paths]
    ids = [row["id"] for row in rows]
    assert len(set(ids)) == len(ids)
    for row in rows:
        assert row["schema"] == "manju.skill-eval/v1"
        assert (ROOT / "skills" / row["skill"] / "SKILL.md").is_file()
        assert isinstance(row["prompt"], str) and row["prompt"].strip()
        assert row["expected"]["must"]
        assert row["expected"]["must_not"]
        assert isinstance(row["review"], str) and row["review"].strip()


def test_contract_examples_use_real_authoring_parsers() -> None:
    marker = chr(96)
    scene_text = (ROOT / "skills" / "scene-design" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    shot_text = (ROOT / "skills" / "shot-design" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    scene_yaml = re.search(marker + "yaml\\s*(.*?)" + marker * 3, scene_text, re.S)
    shot_yaml = re.search(marker + "yaml\\s*(.*?)" + marker * 3, shot_text, re.S)
    assert scene_yaml and shot_yaml
    SceneContract.model_validate(yaml.safe_load(scene_yaml.group(1)))
    ShotSpec.model_validate(yaml.safe_load(shot_yaml.group(1)))


def test_narrative_pacing_is_not_auto_injected() -> None:
    text = (ROOT / "skills" / "narrative-pacing" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    frontmatter = text.split("---", 2)[1]
    assert not re.search(r"^auto:\s*true\s*$", frontmatter, re.M)
    assert "NARRATIVE_FILM" in text
    assert "CTA" in text


def test_core_protocol_names_new_contract_and_readiness_terms() -> None:
    text = (ROOT / "skills" / "manju" / "SKILL.md").read_text(encoding="utf-8")
    for token in (
        "SceneContract",
        "ShotContract",
        "ProviderManifest",
        "TEMP_AUDIO_FOR_ANIMATIC",
        "FINAL_AUDIO_FINISHING",
        "PROOF_SHOT_READY",
        "PROOF_SCENE_READY",
        "BULK_READY",
    ):
        assert token in text

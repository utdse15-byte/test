"""Round V: the SHIPPED skill library's content contract (goal item 1).

test_skills.py proves the LOADER (three-tier resolution, tolerant frontmatter,
index). This file proves the fourteen bundled skills the taxonomy calls for are
actually PRESENT and well-formed craft: every taxonomy id loads, frontmatter is
complete and within the Agent-Skills limits, bodies stay under 500 lines, the
index an agent sees carries a 中文 when_to_use for each, every skill declares a
reference/task type tag, the always-injected core `manju` protocol stays tight
(<300 lines), and — the load-bearing invariant — no skill body smuggles a
vendored LLM call into a system whose engine is LLM-free (§0)."""

from __future__ import annotations

import re

import pytest

from manju.core.skills import (
    CORE_SKILL_ID,
    list_skills,
    load_skill,
    skill_index_text,
    skill_text,
)
from manju.core.skills import _parse_frontmatter  # body extraction, same as loader

# The definitive taxonomy (ROUND-V-REFERENCES-1 §"Consolidated skill taxonomy",
# ids minus the redundant `manju-` prefix since they live under Manju's own
# skills/ dir). manju == the evolved orientation/core protocol (row 1).
TAXONOMY_IDS = {
    CORE_SKILL_ID,
    "creation-funnel",
    "narrative-pacing",
    "shot-design",
    "prompt-craft",
    "character-consistency",
    "subtitle-standards",
    "audio-finishing",
    "cover-and-title",
    "visual-qc-review",
    "series-breakdown",
    "series-bible",
    "repair-loop",
    "skill-authoring",
}

# Code signatures that would mean the ENGINE (or a skill's own scripts) calls an
# LLM directly — forbidden: Manju is agent-neutral and never calls an LLM (§0);
# the driving agent supplies all intelligence. (Skills may *mention* Claude/an
# agent as the reader — that is not a vendored call, hence signature-level match.)
_LLM_CALL_SIGNATURES = (
    "import anthropic",
    "import openai",
    "from anthropic",
    "from openai",
    ".chat.completions",
    "chatcompletion",
    ".messages.create",
    "openai.completion",
    "genai.generativemodel",
    "cohere.client",
)

_CJK = re.compile(r"[一-鿿]")


@pytest.fixture(autouse=True)
def _isolate_user_tier(monkeypatch, tmp_path):
    # No project tier (list_skills(None)) + an empty user tier == exactly the
    # bundled library, so these assertions test what Manju SHIPS.
    monkeypatch.setenv("MANJU_SKILLS_DIR", str(tmp_path / "_empty_user_skills"))


def _bundled():
    rows = list_skills(None)
    assert all(s.source == "bundled" for s in rows), "user/project tier leaked in"
    return rows


def _body(info) -> str:
    _, body = _parse_frontmatter(info.path.read_text(encoding="utf-8"))
    return body


# ------------------------------------------------------- presence & loading


def test_every_taxonomy_id_is_present_and_loads():
    ids = {s.id for s in _bundled()}
    missing = TAXONOMY_IDS - ids
    assert not missing, f"taxonomy skills not shipped: {sorted(missing)}"
    for sid in TAXONOMY_IDS:
        info = load_skill(None, sid)  # resolves + attaches full text on .path
        assert info.path is not None and info.path.is_file()
        assert skill_text(None, sid).strip(), f"{sid} has empty body text"


def test_core_skill_listed_first():
    rows = _bundled()
    assert rows[0].id == CORE_SKILL_ID


# ------------------------------------------------------- frontmatter contract


@pytest.mark.parametrize("sid", sorted(TAXONOMY_IDS))
def test_frontmatter_complete_and_within_limits(sid):
    info = load_skill(None, sid)
    assert info.name and info.name.strip(), f"{sid}: empty name"
    assert info.description and info.description.strip(), f"{sid}: empty description"
    assert info.when_to_use and info.when_to_use.strip(), f"{sid}: empty when_to_use"

    # Agent-Skills hard limits (§1a): name kebab ≤64, no reserved words;
    # description ≤1024. when_to_use renders in the agent-facing index → 中文.
    assert len(info.name) <= 64, f"{sid}: name >64 chars"
    assert re.fullmatch(r"[a-z0-9-]+", info.name), f"{sid}: name not kebab-case"
    lowered = info.name.lower()
    assert "claude" not in lowered and "anthropic" not in lowered, \
        f"{sid}: name contains a reserved word"
    assert len(info.description) <= 1024, f"{sid}: description >1024 chars"
    assert _CJK.search(info.when_to_use), f"{sid}: when_to_use not Chinese"


@pytest.mark.parametrize("sid", sorted(TAXONOMY_IDS))
def test_reference_or_task_type_tag_present(sid):
    info = load_skill(None, sid)
    assert "reference" in info.tags or "task" in info.tags, \
        f"{sid}: must declare a reference/task type tag, got {info.tags}"


# ------------------------------------------------------------- body limits


@pytest.mark.parametrize("sid", sorted(TAXONOMY_IDS))
def test_body_under_500_lines(sid):
    n = len(_body(load_skill(None, sid)).splitlines())
    assert n < 500, f"{sid}: body {n} lines (progressive disclosure: <500, push depth to references/)"


def test_core_protocol_stays_tight():
    # `manju auto` injects the whole core skill in full every run — it must stay
    # small even as the rest of the library grows.
    info = load_skill(None, CORE_SKILL_ID)
    total = len(info.path.read_text(encoding="utf-8").splitlines())
    assert total < 300, f"core manju skill {total} lines (>=300 bloats every auto run)"


# ---------------------------------------------------------------- the index


def test_index_lists_every_skill_with_chinese_when_to_use():
    idx = skill_index_text(None)  # the block agents actually receive
    for s in _bundled():
        assert s.id in idx, f"{s.id} missing from the skill index"
        hint = s.when_to_use or s.description
        assert hint in idx, f"{s.id}: its when_to_use is not shown in the index"
        assert _CJK.search(hint), f"{s.id}: index hint is not Chinese"


# ------------------------------------------------- the engine stays LLM-free


@pytest.mark.parametrize("sid", sorted(TAXONOMY_IDS))
def test_no_skill_body_vendors_an_llm_call(sid):
    body = skill_text(None, sid).lower()
    hits = [sig for sig in _LLM_CALL_SIGNATURES if sig in body]
    assert not hits, (
        f"{sid}: body contains a vendored LLM-call signature {hits} — the Manju "
        f"engine never calls an LLM (§0); intelligence comes from the driving agent."
    )

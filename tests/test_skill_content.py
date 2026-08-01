"""Round V: the SHIPPED skill library's content contract (goal item 1).

test_skills.py proves the LOADER (three-tier resolution, tolerant frontmatter,
index). This file proves the bundled skills are actually PRESENT and well-formed
craft: every taxonomy id loads, frontmatter is complete and within the
Agent-Skills limits, bodies stay under 500 lines, the index an agent sees
carries a 中文 when_to_use for each, every skill declares a reference/task type
tag, the always-injected core `manju` protocol stays tight (<300 lines), and —
the load-bearing invariant — no skill body smuggles a vendored LLM call into a
system whose engine is LLM-free (§0).

文档收口波(2026-08-01)扩了两处,都出自《软能力审计》§六的留档:

* **契约的作用域从 TAXONOMY_IDS 扩到盘上每一个技能。** taxonomy 是"至少要有
  这些",不是"只管这些";超出它的 5 个技能(`error-codes`、
  `continue-from-accepted-take`、`direct-shot-source-patch`、
  `localize-dialogue`、`review-take-and-route-repair`)此前只被"frontmatter
  能解析吗"两条覆盖——名字长度、类型标签、正文行数、LLM 签名一概没查。
* **每个可选技能必须写「什么时候不该用」。** 19 个技能 0 个有。技能库最贵的
  失败不是漏触发,是**误触发**:一个技能被加载进不该它管的场景,agent 照着
  它的决策树走完全程。`when_to_use` 只说"何时用",反面得自己写清楚,而且要
  指出该去哪(另一个技能 id 或一条 `manju` 命令),否则只是废话。
  核心 `manju` 协议豁免:它 `auto: true` 全量注入,从来不被"选择",不存在
  "别加载它"这个决定(下面有一条正面钉守着这个豁免不被滥用)。"""

from __future__ import annotations

import re
from pathlib import Path

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

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"
# Everything Manju SHIPS, not just the required minimum. The taxonomy stays the
# presence contract; the content contract below applies to whatever is on disk.
ALL_SKILL_IDS = sorted(p.parent.name for p in SKILLS_ROOT.glob("*/SKILL.md"))
SELECTABLE_IDS = [sid for sid in ALL_SKILL_IDS if sid != CORE_SKILL_ID]

NOT_USE_HEADING = "什么时候不该用"


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


@pytest.mark.parametrize("sid", ALL_SKILL_IDS)
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


@pytest.mark.parametrize("sid", ALL_SKILL_IDS)
def test_reference_or_task_type_tag_present(sid):
    info = load_skill(None, sid)
    assert "reference" in info.tags or "task" in info.tags, \
        f"{sid}: must declare a reference/task type tag, got {info.tags}"


# ------------------------------------------------------------- body limits


@pytest.mark.parametrize("sid", ALL_SKILL_IDS)
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


@pytest.mark.parametrize("sid", ALL_SKILL_IDS)
def test_no_skill_body_vendors_an_llm_call(sid):
    body = skill_text(None, sid).lower()
    hits = [sig for sig in _LLM_CALL_SIGNATURES if sig in body]
    assert not hits, (
        f"{sid}: body contains a vendored LLM-call signature {hits} — the Manju "
        f"engine never calls an LLM (§0); intelligence comes from the driving agent."
    )


# ------------------------------------------- 什么时候不该用(误触发的解药)


@pytest.mark.parametrize("sid", SELECTABLE_IDS)
def test_every_selectable_skill_says_when_not_to_use(sid):
    """A skill that only says when to USE it over-triggers: the agent loads it
    into a neighbouring scenario and follows its decision tree to the end.
    Every selectable skill must state its negative space."""
    body = skill_text(None, sid)
    assert NOT_USE_HEADING in body, (
        f"{sid}: no 「{NOT_USE_HEADING}」 section — over-triggering has no brake")


@pytest.mark.parametrize("sid", SELECTABLE_IDS)
def test_the_negative_section_routes_somewhere_real(sid):
    """"别用我" without "去用那个" just strands the agent. The section must name
    another shipped skill id or a real `manju` command."""
    body = skill_text(None, sid)
    assert NOT_USE_HEADING in body, f"{sid}: 先补上「{NOT_USE_HEADING}」小节"
    start = body.index(NOT_USE_HEADING)
    nxt = body.find("\n## ", start)
    section = body[start:nxt if nxt != -1 else len(body)]
    others = [o for o in ALL_SKILL_IDS if o != sid and o in section]
    assert others or "manju " in section, (
        f"{sid}: its 「{NOT_USE_HEADING}」 section points nowhere — name the "
        f"skill or the command that DOES cover the case")


def test_the_core_protocols_exemption_is_earned():
    """The exemption above is not a hole: the core protocol is injected in FULL
    on every run (`auto: true`), so it is never *chosen* — there is no
    "don't load this one" decision for a negative section to inform. If it ever
    stops being auto-injected, this test goes red and the exemption goes away."""
    text = (SKILLS_ROOT / CORE_SKILL_ID / "SKILL.md").read_text(encoding="utf-8")
    front = text.split("---")[1]
    assert re.search(r"^auto:\s*true\s*$", front, re.M), (
        "the core skill is no longer auto-injected — it must now declare "
        f"「{NOT_USE_HEADING}」 like every other skill")


# ------------------------------------- 索引可达性 + 渐进式披露的第三层


def test_the_core_index_table_lists_every_selectable_skill():
    """§0's table is how a cold session learns a skill EXISTS. Four skills
    (`continue-from-accepted-take`, `direct-shot-source-patch`,
    `localize-dialogue`, `review-take-and-route-repair`) shipped without ever
    being listed there — reachable only by someone who already ran
    `manju skills`."""
    core = (SKILLS_ROOT / CORE_SKILL_ID / "SKILL.md").read_text(encoding="utf-8")
    missing = [sid for sid in SELECTABLE_IDS if f"`{sid}`" not in core]
    assert not missing, f"核心协议 §0 的技能表漏了: {missing}"


@pytest.mark.parametrize("sid", ALL_SKILL_IDS)
def test_every_referenced_reference_file_exists(sid):
    """Layer 3 of progressive disclosure is a PATH the agent opens — a pointer
    to a file that is not there costs a failed read and the depth is lost."""
    body = skill_text(None, sid)
    for rel in set(re.findall(r"references/[\w./-]+\.md", body)):
        assert (SKILLS_ROOT / sid / rel).is_file(), \
            f"{sid}: 正文指向 {rel},但文件不存在"


# --------------------------------------- every skill on disk, not just taxonomy


def _all_skill_files() -> list[Path]:
    root = Path(__file__).resolve().parent.parent / "skills"
    return sorted(root.rglob("SKILL.md"), key=lambda p: p.as_posix())


@pytest.mark.parametrize("path", _all_skill_files(), ids=lambda p: p.parent.name)
def test_frontmatter_actually_parses(path: Path) -> None:
    """The loader is deliberately TOLERANT — a malformed skill must not crash
    the CLI, so `_parse_frontmatter` swallows the YAML error and returns {}.
    The cost is that an authoring mistake is invisible: the skill still loads,
    and `manju skills` quietly prints the H1 heading where `when_to_use`
    belongs, so the agent-facing index degrades with nothing to notice.

    Caught the hard way: a `description` containing "code: \"error\"" — a
    colon-space inside an unquoted YAML scalar — silently lost every key in
    the block. The tolerance belongs at runtime; the loudness belongs here.

    Scoped to every skill ON DISK rather than TAXONOMY_IDS, which is the
    required-minimum set: the skills added beyond it were exactly the ones no
    frontmatter check covered.
    """
    import yaml

    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    assert m, f"{path.parent.name}: no YAML frontmatter block"
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:  # pragma: no cover - the message is the point
        raise AssertionError(
            f"{path.parent.name}: frontmatter is not valid YAML — the loader "
            f"will silently drop every key: {str(exc).splitlines()[0]}"
        ) from None
    assert isinstance(data, dict), f"{path.parent.name}: frontmatter not a mapping"
    for key in ("name", "description", "when_to_use"):
        assert data.get(key), f"{path.parent.name}: frontmatter missing {key!r}"


@pytest.mark.parametrize("path", _all_skill_files(), ids=lambda p: p.parent.name)
def test_the_index_shows_when_to_use_not_the_heading(path: Path) -> None:
    """The observable symptom of the bug above, asserted directly: what the
    agent-facing index prints must be the declared when_to_use."""
    info = load_skill(None, path.parent.name)
    assert info.when_to_use, f"{path.parent.name}: no when_to_use loaded"
    assert not info.when_to_use.lstrip().startswith("#"), (
        f"{path.parent.name}: index is showing a heading — frontmatter lost")

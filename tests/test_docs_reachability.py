"""软能力波(2026-07-31):文档可达性的钉。

三条,全部由实地勘察坐实:

1. **裸 `#N` 引用会指错人。** DECISIONS.md 有七个段落各自从 1 重新编号
   (顶层 1–51 与 UX-REAL-USE 1–33、TRISURFACE-FIX 1–30 重叠),而
   CLAUDE.md 用裸 `#33` 给 ffmpeg 6.1.1 钉作证 —— 顺着找到的是无关的
   顶层 `## 33`,真正的证据在 `UX-REAL-USE #33`。**唯一一条本来能救一天的
   指引,指错了地方。** 规则:裸 `#N` 只指顶层;命名段落必须带段名。
2. **索引漏掉了最近三个月。** 索引自述用途是"让会话不用通读就能找到管着
   某文件的决定",却只覆盖顶层 1–51,六个命名段落的 92 条一条未收。
3. **教程已经做好了却没人知道。** `manju new --demo` 出 12 镜零花费样片
   (实测 2m19s 出片),而 README/docs/CLAUDE.md 里 `--demo` 出现 0 次。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DECISIONS = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
CLAUDE_MD = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

# Files that cite decisions in prose. Source comments are included: a wrong
# pointer in a comment misleads exactly as much as one in a doc.
_CITING = [ROOT / "CLAUDE.md", ROOT / "README.md",
           *(ROOT / "tests").glob("test_*.py"),
           *(ROOT / "src" / "manju").rglob("*.py"),
           *(ROOT / "skills").rglob("*.md"),
           *(ROOT / "docs").glob("*.md")]


def _named_sections() -> dict[str, int]:
    """section name → highest entry number inside it."""
    out: dict[str, int] = {}
    current = None
    for line in DECISIONS.splitlines():
        m = re.match(r"^## ([A-Z][A-Z0-9\-]+)\s*\(\d{4}-\d{2}-\d{2}\)", line)
        if m:
            current = m.group(1)
            out.setdefault(current, 0)
            continue
        if line.startswith("## "):
            current = None
            continue
        if current:
            m2 = re.match(r"^(\d+)\. ", line)
            if m2:
                out[current] = max(out[current], int(m2.group(1)))
    return out


def _top_level_numbers() -> set[int]:
    return {int(m) for m in re.findall(r"^## (\d+)[.a-z]", DECISIONS, re.M)}


def test_the_numbering_scopes_really_do_collide():
    """The hazard is real, not hypothetical — this test states WHY the rule
    below exists, so a future session cannot dismiss it as pedantry."""
    named = _named_sections()
    assert named, "no named decision sections found — parser drifted"
    top = _top_level_numbers()
    overlapping = {s: n for s, n in named.items() if any(i in top for i in range(1, n + 1))}
    assert overlapping, "scopes no longer collide — this pin can be retired"


def test_every_bare_decision_reference_resolves_to_a_top_level_entry():
    """Rule: a BARE ``DECISIONS #N`` means the top-level ``## N.`` entry.
    A citation whose number only exists inside a named section must name that
    section (``UX-REAL-USE #33``), or a reader lands on an unrelated entry."""
    top = _top_level_numbers()
    offenders: list[str] = []
    for path in _CITING:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if path.name == Path(__file__).name:
            continue
        for m in re.finditer(r"DECISIONS(?:\.md)? #(\d+)", text):
            if int(m.group(1)) not in top:
                offenders.append(f"{path.relative_to(ROOT)}: #{m.group(1)}")
    assert offenders == [], (
        "bare #N citations that do not exist at top level — qualify them with "
        f"the section name (e.g. `UX-REAL-USE #33`): {offenders}")


def test_every_named_section_entry_is_in_the_index():
    """The index exists so a session can find what governs a file without a
    full read; 92 entries were invisible to it until 2026-07-31."""
    named = _named_sections()
    index_rows = set(re.findall(r"^\| `([A-Z][A-Z0-9\-]+ #\d+)`", DECISIONS, re.M))
    missing = [f"{sec} #{i}"
               for sec, top in named.items()
               for i in range(1, top + 1)
               if f"{sec} #{i}" not in index_rows]
    assert missing == [], f"decisions absent from the index: {missing[:12]}"


def test_the_index_rule_is_stated_where_a_reader_will_hit_it():
    head = DECISIONS[:DECISIONS.index("## 1. ")]
    assert "编号域" in head           # the rule has a name
    assert "UX-REAL-USE #33" in head  # …and a worked example of the trap


# ------------------------------------------------- the tutorial exists in prose


def test_the_zero_cost_demo_is_documented_where_a_beginner_looks():
    """`manju new --demo` is a free pipeline diagnostic, never narrative proof."""
    assert "--demo" in README
    # The tutorial is a SECTION, not a passing mention — anchor on the heading
    # so a stray `--demo` elsewhere can never satisfy this pin.
    heads = [h for h in re.findall(r"^## .*$", README, re.M) if "系统体检样片" in h]
    assert heads, "no tutorial section heading found in README"
    start = README.index(heads[0])
    section = README[start:README.index("\n## ", start + 1)]
    assert "manju new" in section and "--demo" in section
    assert "manju build --yes" in section     # the second half of the recipe
    assert "renders/final" in section         # what the reader should find
    assert "零" in section                     # …and that it costs nothing
    assert "manju status" in section          # …and where to go next
    assert "pipeline regression sample" in section
    assert "proxy-only" in section
    assert "不证明" in section


def test_the_quickstart_selects_a_file_it_actually_imported():
    """Copy-paste fidelity: the Quickstart imported 开场.mp4 / 雨夜.mp4 and
    then selected `media/imports/opening.mp4`, which was never imported."""
    start = README.index("## Quickstart")
    block = README[start:start + 1800]
    imported = set(re.findall(r"manju import ([^\n#]+)", block))
    selected = re.findall(r"--file (\S+)", block)
    assert selected, "Quickstart no longer demonstrates select --file"
    for path in selected:
        stem = Path(path).name
        assert any(stem in group for group in imported), (
            f"Quickstart selects {stem!r}, which no `manju import` line above "
            f"ever imported (imports: {imported})")


@pytest.mark.parametrize("doc", ["README.md", "CLAUDE.md"])
def test_the_owner_facing_docs_point_at_the_repo_state_file(doc):
    """A returning owner needs the 'where was I' file; README never linked it."""
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert "STATE.md" in text


# ------------------------------------------- the MCP freeze reached the skills


SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))


def test_the_core_protocol_tells_a_session_its_surface_is_the_cli():
    """The owner froze MCP on 2026-07-31 ("有 CLI 和 GUI 就可以了"). The freeze
    landed in CLAUDE.md and DECISIONS the same day but NOT in the skills, so
    the operating protocol kept teaching MCP as a primary path."""
    core = (ROOT / "skills" / "manju" / "SKILL.md").read_text(encoding="utf-8")
    assert "冻结" in core
    assert "TRISURFACE-FIX #25" in core          # the decision is cited, not asserted
    head = core[:core.index("## 1.")]
    assert "CLI" in head                          # …and the surface is named up front


def test_no_skill_mandates_an_mcp_only_step():
    """`skills/manju/SKILL.md:57` made get_shot/update_shot + expected_rev
    MANDATORY — an MCP-only vocabulary with NO CLI equivalent (`expected_rev`
    appears in cli.py solely inside the serve-mcp docstring). A CLI session was
    being ordered to perform an impossible step."""
    mcp_only = ("expected_rev", "get_shot", "update_shot", "skill_show", "skill_list")
    offenders = []
    for path in SKILLS:
        text = path.read_text(encoding="utf-8")
        for token in mcp_only:
            for m in re.finditer(re.escape(token), text):
                # A frozen-surface token is allowed ONLY inside an explicitly
                # archival passage. Proximity, not file-level presence: a single
                # 冻结 elsewhere in the file must not launder every mention
                # (a whole-file exemption would make this pin self-satisfying).
                window = text[max(0, m.start() - 400):m.end() + 400]
                if "冻结" not in window:
                    offenders.append(f"{path.parent.name}: {token}")
                    break
    assert offenders == [], (
        "skills still teach MCP-only vocabulary as a live path: " + str(offenders))


def test_the_mcp_code_table_is_marked_frozen_not_deleted():
    """The freeze keeps the paid-for surface green — the table stays, but a
    reader must not mistake it for the recommended路径."""
    text = (ROOT / "skills" / "error-codes" / "SKILL.md").read_text(encoding="utf-8")
    assert "## MCP 工具面的 code" in text          # not deleted
    heading = text[text.index("## MCP 工具面的 code"):][:400]
    assert "冻结" in heading and "CLI" in heading   # …and clearly demoted

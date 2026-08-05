"""文档收口波(2026-08-01):把《软能力审计》§六「明知而未做」逐条关掉。

那份报告(REPORTS/SOFT_CAPABILITY_2026-07-31.md)结尾留了一张"知道、但这波
没做"的清单。留档不是免责——每条都是**文档自己说了假话或压根找不到**的具体
形态,本文件把修复钉住:

1. **README 583 行、没有目录**,其中一半是 90 行的命令参考表。店主想找一条
   命令只能从头滚;而那张表的读者其实是 AI 会话。表挪进 `docs/CLI.md`,
   README 头部给目录。
2. **`docs/DESIGN_v2.1.md` 与 v2.2 约 82% 重复,且没有任何"已被取代"标记。**
   一个冷启动会话按文件名排序读到的是 v2.1 —— 过时的那份。
3. **`REPORTS/LAST_GREEN.yaml` 自称 "written by CI",但仓库里没有任何 workflow
   调用 `scripts/dev/update_last_green.py`。** 两个 `pending` 不是"还没绿",是
   "没人盖章"——一份没人写的状态文件,读它的人会以为门禁从没双绿过。
   实测取证:PR #52 的头 `efd7783` 与合并后的 `c7889b8` **树相同**
   (`aa87368…`),它在 windows run 30704812522 与 ubuntu run 30704812542 上
   双绿。盖章用真数据,测试数量没取到就留 null——不编。
4. 技能面的两条(孤儿技能、缺「什么时候不该用」)钉在 tests/test_skill_content.py。

留在清单上没做的仍然只有一条:XDG/`%APPDATA%` 配置位置(迁移既有 `~/.manju`
路径,风险大于收益,DECISIONS `SOFT-CAP` 已留档)。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
CLI_DOC_PATH = ROOT / "docs" / "CLI.md"
LAST_GREEN_PATH = ROOT / "REPORTS" / "LAST_GREEN.yaml"

TOC_HEADING = "目录"


def _headings(text: str) -> list[str]:
    return [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]


def _slug(heading: str) -> str:
    """GitHub 的锚点算法(去反引号与标点、小写、空格转连字符、CJK 保留)。

    测试与 README 用同一个函数,锚点因此按构造一致。"""
    s = heading.strip().lower().replace("`", "")
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    return s.strip().replace(" ", "-")


def _manju_table_rows(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("| `manju")]


# --------------------------------------------------------- ① README 可导航


def test_readme_carries_a_table_of_contents_for_every_section():
    heads = _headings(README)
    assert TOC_HEADING in heads, "README 仍然没有目录——583 行只能从头滚"
    start = README.index(f"## {TOC_HEADING}")
    toc = README[start:README.index("\n## ", start + 1)]
    missing = [h for h in heads if h != TOC_HEADING and f"(#{_slug(h)})" not in toc]
    assert not missing, f"目录漏了这些小节: {missing}"


def test_the_table_of_contents_is_reachable_without_scrolling():
    """目录必须在第一屏——放在文末的目录等于没有。"""
    line_no = README.splitlines().index(f"## {TOC_HEADING}")
    assert line_no < 60, f"目录在第 {line_no} 行,太深了"


def test_every_readme_anchor_is_unique():
    slugs = [_slug(h) for h in _headings(README)]
    dupes = {s for s in slugs if slugs.count(s) > 1}
    assert not dupes, f"重复锚点,目录会跳错地方: {dupes}"


def test_the_command_reference_moved_into_its_own_doc():
    assert CLI_DOC_PATH.exists(), "docs/CLI.md 不存在"
    moved = _manju_table_rows(CLI_DOC_PATH.read_text(encoding="utf-8"))
    assert len(moved) >= 50, f"命令表没搬全,只有 {len(moved)} 行"
    left = _manju_table_rows(README)
    assert len(left) <= 8, f"README 里还留着 {len(left)} 行命令表"
    assert "docs/CLI.md" in README, "README 没有指向搬走的命令参考"


# ------------------------------------------------------------ ② 归档 v2.1


def test_the_superseded_design_doc_is_archived_and_says_so():
    old = ROOT / "docs" / "DESIGN_v2.1.md"
    archived = ROOT / "docs" / "archive" / "DESIGN_v2.1.md"
    assert not old.exists(), "v2.1 仍在 docs/ 主目录,与 v2.2 并列"
    assert archived.exists(), "v2.1 没有归档副本(历史不该删,只该降级)"
    head = archived.read_text(encoding="utf-8")[:800]
    assert "DESIGN_v2.2" in head, "归档件没有指向现行设计"
    assert "取代" in head or "superseded" in head.lower()


def test_no_live_doc_points_at_the_retired_design_path():
    """DECISIONS/REPORTS 是历史账,允许留下旧路径;活文档不允许。"""
    live = [ROOT / "README.md", ROOT / "CLAUDE.md", ROOT / "STATE.md"]
    live += [p for p in (ROOT / "docs").glob("*.md")]
    live += list((ROOT / "skills").glob("*/SKILL.md"))
    offenders = [p.name for p in live
                 if "docs/DESIGN_v2.1.md" in p.read_text(encoding="utf-8")]
    assert not offenders, f"这些活文档仍指向已归档的 v2.1: {offenders}"


# ------------------------------------------------- ③ LAST_GREEN 说的是不是真话


def _last_green() -> dict:
    return yaml.safe_load(LAST_GREEN_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("platform", ["ubuntu", "windows"])
def test_last_green_never_claims_success_without_the_evidence(platform):
    """success 必须带得出证据:一个真 SHA + 那条平台的 run id + 时间戳。
    双向钉——将来手滑把 pending 改成 success 而不填证据,这里当场红。"""
    data = _last_green()
    row = data[platform]
    assert row["result"] in {"success", "failure", "pending"}
    if row["result"] != "success":
        return
    assert re.fullmatch(r"[0-9a-f]{40}", str(data["commit"])), \
        "声称双绿却没有 40 位 commit SHA"
    assert row["workflow_run"], f"{platform} 声称 success 却没有 run id"
    assert data["generated_at"], "没有盖章时间"


def test_the_stamping_claim_matches_what_the_workflows_actually_do():
    """这条是本波的成因:文件头写着 "written by CI",而 .github/workflows/
    里没有任何一处调用生成器。要么把 CI 接上,要么把话说对——不许两不沾。"""
    header = LAST_GREEN_PATH.read_text(encoding="utf-8")
    workflows = "\n".join(p.read_text(encoding="utf-8")
                          for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    claims_ci = re.search(r"written by CI|由 ?CI (写|盖)", header)
    if claims_ci:
        assert "update_last_green" in workflows, (
            "LAST_GREEN.yaml 自称由 CI 盖章,但没有任何 workflow 调用 "
            "scripts/dev/update_last_green.py")


def test_last_green_is_never_a_build_input():
    """派生报告永不是构建输入(README §disciplines)——含这一份。"""
    readers = [p.relative_to(ROOT).as_posix()
               for p in (ROOT / "src").rglob("*.py")
               if "LAST_GREEN" in p.read_text(encoding="utf-8")]
    assert not readers, f"src/ 读了派生状态文件: {readers}"


def test_the_generator_still_matches_the_files_shape():
    """盖章脚本与被盖的文件必须是同一形状——否则手动盖章会写坏结构。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_ulg", ROOT / "scripts" / "dev" / "update_last_green.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    import argparse

    record = mod.build_record(argparse.Namespace(
        commit="0" * 40, ubuntu_run="1", ubuntu_tests=None,
        windows_run="2", windows_tests=None, generated_at="2026-08-01T00:00:00Z"))
    assert set(record) == set(_last_green()), "生成器与文件的键不一致"
    assert set(record["ubuntu"]) == set(_last_green()["ubuntu"])

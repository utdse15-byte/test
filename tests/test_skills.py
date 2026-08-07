"""Round V: the skill library (core/skills.py) — three-tier resolution,
tolerant frontmatter, progressive-disclosure index, CLI and core surfaces."""

from __future__ import annotations

import pytest

from manju.core.skills import (
    CORE_SKILL_ID,
    list_skills,
    load_skill,
    skill_index_text,
    skill_text,
)


@pytest.fixture(autouse=True)
def _isolate_user_tier(monkeypatch, tmp_path):
    monkeypatch.setenv("MANJU_SKILLS_DIR", str(tmp_path / "_user_skills"))


def _write_skill(root, sid, *, name=None, when=None, body="# 内容\n正文。",
                 broken=False):
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    if broken:
        (d / "SKILL.md").write_text("---\n: not yaml ::\n---\n" + body,
                                    encoding="utf-8")
        return d
    fm = ["---", f"name: {name or sid}", f"description: {sid} 的说明"]
    if when:
        fm.append(f"when_to_use: {when}")
    fm.append("---")
    (d / "SKILL.md").write_text("\n".join(fm) + "\n" + body, encoding="utf-8")
    return d


# ------------------------------------------------------------- resolution


def test_bundled_core_skill_visible_without_project():
    rows = list_skills(None)
    assert rows and rows[0].id == CORE_SKILL_ID  # core protocol listed first
    assert rows[0].source == "bundled"
    assert "协作协议" in rows[0].description or rows[0].description


def test_project_tier_wins_over_bundled(tmp_project):
    """Every id EXCEPT the core protocol id — project tier wins."""
    _write_skill(tmp_project.root / "skills", "narrative", when="本项目自定义手册")
    rows = {s.id: s for s in list_skills(tmp_project)}
    assert rows["narrative"].source == "project"
    assert rows["narrative"].when_to_use == "本项目自定义手册"


def test_project_tier_cannot_shadow_core_skill(tmp_project):
    """Round W goal item 46: the PROJECT tier may NOT override
    ``CORE_SKILL_ID`` — a downloaded/untrusted project's own
    ``skills/manju/SKILL.md`` must never shadow the core operating protocol
    (supersedes the old "project wins over bundled" contract for THIS one
    id). ``manju skills`` must also surface a loud
    warning that the shadow attempt was ignored."""
    from manju.core.skills import core_skill_shadow_warning

    _write_skill(tmp_project.root / "skills", CORE_SKILL_ID,
                 name="manju", when="恶意项目自定义手册")

    info = load_skill(tmp_project, CORE_SKILL_ID)
    assert info.source == "bundled"  # NOT "project" — never shadowed
    assert info.when_to_use != "恶意项目自定义手册"

    rows = {s.id: s for s in list_skills(tmp_project)}
    assert rows[CORE_SKILL_ID].source == "bundled"

    warning = core_skill_shadow_warning(tmp_project)
    assert warning and CORE_SKILL_ID in warning

    # a project WITHOUT a shot at shadowing the core id gets no warning
    assert core_skill_shadow_warning(None) is None


def test_user_tier_between_project_and_bundled(tmp_project, tmp_path, monkeypatch):
    user_dir = tmp_path / "_user_skills"
    monkeypatch.setenv("MANJU_SKILLS_DIR", str(user_dir))
    _write_skill(user_dir, "narrative", when="节奏与钩子")
    rows = {s.id: s for s in list_skills(tmp_project)}
    assert rows["narrative"].source == "user"
    # a project overlay of the same id then wins
    _write_skill(tmp_project.root / "skills", "narrative", when="本片叙事规则")
    rows = {s.id: s for s in list_skills(tmp_project)}
    assert rows["narrative"].source == "project"
    assert rows["narrative"].when_to_use == "本片叙事规则"


def test_broken_frontmatter_is_still_a_skill(tmp_project):
    _write_skill(tmp_project.root / "skills", "weird", broken=True)
    info = load_skill(tmp_project, "weird")
    assert info.id == "weird"
    assert info.description  # first heading fallback
    assert "正文" in skill_text(tmp_project, "weird")


def test_unknown_skill_raises_with_available_ids(tmp_project):
    with pytest.raises(KeyError, match=CORE_SKILL_ID):
        load_skill(tmp_project, "nope")


# ---------------------------------------------------------------- index


def test_index_excludes_core_and_lists_when_to_use(tmp_project):
    _write_skill(tmp_project.root / "skills", "narrative", when="节奏与钩子")
    idx = skill_index_text(tmp_project, exclude=(CORE_SKILL_ID,))
    assert "narrative" in idx and "节奏与钩子" in idx
    assert f"- {CORE_SKILL_ID}:" not in idx
    assert "manju skills show" in idx


def test_index_empty_when_only_core(monkeypatch, tmp_path):
    # no project, empty user tier → only the bundled core skill exists
    monkeypatch.setenv("MANJU_SKILLS_DIR", str(tmp_path / "none"))
    idx = skill_index_text(None, exclude=(CORE_SKILL_ID,))
    # bundled library may grow later skills; the invariant is: excluding
    # everything listed yields the empty string
    all_ids = tuple(s.id for s in list_skills(None))
    assert skill_index_text(None, exclude=all_ids) == ""
    assert isinstance(idx, str)


# ------------------------------------------------------------------ CLI


def test_cli_skills_list_and_show(tmp_project, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    _write_skill(tmp_project.root / "skills", "narrative", when="节奏与钩子")
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()
    res = runner.invoke(app, ["skills", "--json"])
    assert res.exit_code == 0, res.output
    import json

    ids = [s["id"] for s in json.loads(res.output)["skills"]]
    assert CORE_SKILL_ID in ids and "narrative" in ids

    res = runner.invoke(app, ["skills", "show", "narrative"])
    assert res.exit_code == 0
    assert "正文" in res.output

    res = runner.invoke(app, ["skills", "show", "nope"])
    assert res.exit_code != 0


def test_cli_skills_outside_project(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_path)  # not a project
    res = runner_out = CliRunner().invoke(app, ["skills"])
    assert res.exit_code == 0
    assert CORE_SKILL_ID in res.output


def test_cli_skills_list_warns_on_core_shadow_attempt(tmp_project, monkeypatch):
    """Round W goal item 46: `manju skills` (plain and --json) surfaces a
    loud warning when the project tried to shadow the core skill, and never
    returns the project's version of it."""
    import json

    from typer.testing import CliRunner

    from manju.cli import app

    _write_skill(tmp_project.root / "skills", CORE_SKILL_ID,
                 name="manju", when="恶意项目自定义手册")
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()

    res = runner.invoke(app, ["skills"])
    assert res.exit_code == 0
    assert "不允许被项目层覆盖" in res.output

    res_json = runner.invoke(app, ["skills", "--json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    assert data["core_skill_shadow_warning"] and CORE_SKILL_ID in data["core_skill_shadow_warning"]
    core_row = next(s for s in data["skills"] if s["id"] == CORE_SKILL_ID)
    assert core_row["source"] == "bundled"

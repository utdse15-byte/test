"""The `--json` error envelope is a contract; it has to be documented truthfully.

`_fail` says `code` is "a stable machine token an agent can branch on", and 177
assertions across the suite pin specific codes. But the always-injected agent
skill never mentioned the envelope at all: an agent got
`{"error": …, "code": "unknown_shot"}` with no way to know what codes exist,
which are worth branching on, or — the thing that actually matters — that
`"error"` is the UNCLASSIFIED default and covers the large majority of
failures.

The skill now documents it. This file keeps the documentation honest, in the
direction that matters most: every code the docs name must really exist in the
CLI. Documenting a code that no call site emits would send an agent looking for
a branch that can never be taken — a fabricated contract, which is worse than
no contract because it reads as one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLI = (ROOT / "src" / "manju" / "cli.py").read_text(encoding="utf-8")
CORE = (ROOT / "skills" / "manju" / "SKILL.md").read_text(encoding="utf-8")
SKILL = (ROOT / "skills" / "error-codes" / "SKILL.md").read_text(encoding="utf-8")

_START = "## 带专门 code 的失败"
_END = "## 别和降级链"


def _section() -> str:
    """The four classification blocks — where the codes are actually claimed."""
    assert _START in SKILL, "the classification table is gone from the error-codes skill"
    body = SKILL[SKILL.index(_START):]
    return body[:body.index(_END)] if _END in body else body


def _emitted_codes() -> set[str]:
    return set(re.findall(r'code=["\']([a-z_]+)["\']', CLI))


# A line that is nothing BUT backticked tokens is a code list. Prose mentions
# (`manju check`, `unlock`, `ask_before`) sit inside sentences and are skipped,
# so the check reads the claim rather than every backtick on the page — an
# earlier version swept up command names and the provider kinds the skill
# deliberately contrasts against, and reported seven phantom "invented" codes.
_CODE_LINE = re.compile(r"^(?:`[a-z_]+`\s*)+$")


def _documented_codes() -> set[str]:
    codes: set[str] = set()
    for line in _section().splitlines():
        if _CODE_LINE.match(line.strip()):
            codes |= set(re.findall(r"`([a-z_]+)`", line))
        elif line.startswith("|") and "---" not in line:
            cells = line.split("|")
            if len(cells) > 2:
                codes |= set(re.findall(r"`([a-z_]+)`", cells[1]))
    return codes - {"error"}


# ------------------------------------------------------- no invented contract


def test_every_documented_code_is_really_emitted() -> None:
    """The anti-fabrication check. A code in the docs that no call site emits
    is a branch an agent can never take."""
    invented = sorted(_documented_codes() - _emitted_codes())
    assert invented == [], f"documented but never emitted: {invented}"


def test_every_emitted_code_is_documented() -> None:
    """The other direction: a code an agent can receive but cannot look up."""
    undocumented = sorted(_emitted_codes() - _documented_codes())
    assert undocumented == [], f"emitted but undocumented: {undocumented}"


def test_the_docs_do_not_understate_how_common_the_default_is() -> None:
    """The single most useful sentence for an agent is that `error` means
    unclassified. It stays true only while the default really dominates —
    if someone classifies most call sites, this text needs rewriting."""
    explicit = len(re.findall(r"_fail\([^)]*code=", CLI))
    total = len(re.findall(r"_fail\(", CLI))
    assert total > explicit, "no default-coded failures left"
    assert explicit / total < 0.5, (
        f"{explicit}/{total} failures now carry an explicit code — the skill "
        "says the default dominates; rewrite that paragraph")
    assert "未分类" in SKILL and "未分类" in CORE


def test_both_skills_say_json_stays_json_on_failure() -> None:
    """Why an agent can rely on one parse path for success and failure."""
    for doc in (SKILL, CORE):
        assert '"error"' in doc and '"code"' in doc
        assert "退出码非 0" in doc


def test_the_provider_failure_kinds_are_not_confused_with_cli_codes() -> None:
    """`content_rejected`/`rate_limited` live in the run ledger, not here;
    conflating the two vocabularies sends an agent to the wrong remedy."""
    assert "content_rejected" in SKILL
    assert "manju tasks" in SKILL


# ------------------------------------------------- the core skill stays a hub


def test_the_core_protocol_points_at_the_full_table() -> None:
    """The core skill is injected in FULL on every `manju auto` run, so the
    52-code table cannot live there (an existing test caps it under 300 lines,
    and that cap is the agent's token budget). The core carries the one fact an
    agent needs BEFORE it ever hits an error, plus the pointer."""
    assert "manju skills show error-codes" in CORE
    assert "error-codes" in CORE[:CORE.index("## 1.")], (
        "the skill index does not list error-codes, so an agent scanning the "
        "index will not know it exists")


# ------------------------------------------- one fact, one code, every command


@pytest.mark.parametrize("argv", [
    ["select", "S099", "1"],
    ["redo", "S099"],
    ["impact", "S099"],
    ["voice", "S099"],
])
def test_an_unknown_shot_is_unknown_shot_everywhere(
        argv, tmp_project, monkeypatch) -> None:
    """The rule the skill states: the same FACT gets the same code whichever
    command surfaced it. `impact` used to answer `impact_error` — a code shaped
    like the command rather than the fact, so an agent branching on
    `unknown_shot` silently missed it."""
    import json

    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_project.root)
    res = CliRunner().invoke(app, [*argv, "--json"])
    assert res.exit_code != 0, res.output
    payload = json.loads(res.output)
    assert payload["code"] == "unknown_shot", (argv, payload)


def test_the_unknown_shot_message_still_names_the_remedies(
        tmp_project, monkeypatch) -> None:
    """A stable code is for the agent; the message is for the human reading
    the same line. Both have to survive."""
    import json

    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_project.root)
    res = CliRunner().invoke(app, ["impact", "S099", "--json"])
    payload = json.loads(res.output)
    assert "manju status" in payload["error"]
    assert "S099" in payload["error"]

"""F0 Contract Governance — documentation validation (roadmap §4.9).

Two guarantees:

  1. Every ``manju <cmd>`` written in a README command-table row resolves to a
     real command or sub-app in the live typer registry. A renamed/removed
     command whose README row was not updated fails RED here.

  2. The §8 / 14_21-closeout corrected claims stay true: the 更正块 heading is
     present in the completion report, the README toolmap row calls the tool a
     "resolver" (not a dispatcher), and the code anchors those corrections name
     (the bridge service seam, the resolver functions, the real bridge
     commands) still exist.

Prose ``manju`` mentions that are deliberately not commands may be excluded via
ALLOWLIST (currently empty — every command-table span resolves today).
"""

from __future__ import annotations

import re
from pathlib import Path

import typer

from manju.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
# UX wave 2 item 13: the closed AI_IDE_* era lives under REPORTS/archive/.
COMPLETION_19 = REPO_ROOT / "REPORTS" / "archive" / "AI_IDE_19_COMPLETION.md"

# normalized command strings that are prose, not real invocations. Empty today.
ALLOWLIST: frozenset[str] = frozenset()

_SPAN = re.compile(r"`([^`]+)`")
_MANJU = re.compile(r"^manju\s+(.+)$")


def _registry_view() -> tuple[set[str], dict[str, set[str]]]:
    """(top-level leaf command names, {group_name: {subcommand names}})."""
    click_cmd = typer.main.get_command(app)
    top_leaf: set[str] = set()
    groups: dict[str, set[str]] = {}
    for name, child in click_cmd.commands.items():
        if getattr(child, "commands", None):
            groups[name] = set(child.commands.keys())
        else:
            top_leaf.add(name)
    return top_leaf, groups


def _strip_placeholders(s: str) -> str:
    prev = None
    while prev != s:  # remove innermost [...] repeatedly (handles nesting)
        prev = s
        s = re.sub(r"\[[^\[\]]*\]", " ", s)
    s = re.sub(r"<[^>]*>", " ", s)               # <placeholder>
    return s.replace("…", " ").replace("...", " ")


def _clean_tokens(seg: str) -> list[str]:
    toks: list[str] = []
    for t in seg.split():
        if not t or t.startswith(("-", "|")):
            continue
        if re.fullmatch(r"[A-Z0-9_]{1,4}", t):   # FILE, S002, N, VO, A, B
            continue
        if re.fullmatch(r"[A-Z][a-zA-Z]*", t):   # ID, File-style placeholders
            continue
        toks.append(t)
    return toks


def _resolve_segment(tokens: list[str], prev_group: str | None,
                     top_leaf: set[str], groups: dict[str, set[str]]
                     ) -> tuple[bool, str | None, str]:
    """Resolve one ` / `-separated invocation segment. Returns
    (ok, group-context-for-next-segment, reason)."""
    if not tokens:
        return True, prev_group, "empty"
    t0 = tokens[0]
    cands = t0.split("/")  # slash-alternates, e.g. list/add or analyze/tool

    # shared-prefix rescue: `board scene / keyframes` — the 2nd segment drops the
    # shared group, so a bare subcommand inherits the previous segment's group.
    if prev_group and all(c not in top_leaf and c not in groups for c in cands):
        if all(c in groups.get(prev_group, set()) for c in cands):
            return True, prev_group, f"{prev_group} {t0}"

    resolved_group: str | None = None
    for c in cands:
        if c in top_leaf:
            continue  # leaf command; the rest of the tokens are args
        if c in groups:
            resolved_group = c
            rest = _clean_tokens(" ".join(tokens[1:]))
            if rest:
                subs = rest[0].split("/")
                bad = [x for x in subs if x not in groups[c]]
                if bad:
                    return False, c, f"unknown subcommand(s) {bad} under {c!r}"
        else:
            return False, prev_group, f"unknown command/sub-app {c!r}"
    return True, resolved_group or prev_group, "ok"


def _iter_readme_command_spans() -> list[str]:
    spans: list[str] = []
    for line in README.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "`" not in line:
            continue
        for span in _SPAN.findall(line):
            if _MANJU.match(span.strip()):
                spans.append(span.strip())
    return spans


# ------------------------------------------------------- README command checks


def test_readme_command_spans_are_found():
    """The parser actually matches the command table (guards against a silent
    zero-match that would make the resolution test vacuously pass)."""
    assert len(_iter_readme_command_spans()) >= 50


def test_every_readme_command_resolves():
    """Every `manju <cmd>` in the command table resolves to a real command."""
    top_leaf, groups = _registry_view()
    failures: list[str] = []
    for span in _iter_readme_command_spans():
        body = _strip_placeholders(_MANJU.match(span).group(1))
        prev_group: str | None = None
        for seg in re.split(r"\s+/\s+", body):
            tokens = _clean_tokens(seg)
            if " ".join(tokens) in ALLOWLIST:
                continue
            ok, prev_group, reason = _resolve_segment(tokens, prev_group, top_leaf, groups)
            if not ok:
                failures.append(f"`{span}` -> {reason}")
    assert not failures, (
        "README command-table row(s) reference commands that no longer exist "
        "(rename/remove the row, or add a prose mention to ALLOWLIST):\n  "
        + "\n  ".join(failures)
    )


# ----------------------------------------------- §8 / closeout corrected claims


def test_completion_report_has_correction_block_heading():
    """Claim: the 14_21 closeout 更正块 stays in AI_IDE_19_COMPLETION.md."""
    text = COMPLETION_19.read_text(encoding="utf-8")
    assert "更正块" in text, "AI_IDE_19_COMPLETION.md lost its 更正块 heading"


def test_readme_toolmap_row_says_resolver():
    """Claim 5: the toolmap row calls `manju tool` a whitelist RESOLVER (it
    resolves/validates intent ops, it never dispatches/invokes executors)."""
    toolmap_rows = [
        line for line in README.read_text(encoding="utf-8").splitlines()
        if line.startswith("|") and "manju tool" in line
    ]
    assert toolmap_rows, "could not find the README toolmap row (mentions `manju tool`)"
    assert any("resolver" in line for line in toolmap_rows), (
        "the toolmap row must describe the tool as a 'resolver', not a dispatcher")


def test_corrected_claims_have_live_code_anchors():
    """The corrections name concrete code — assert it still exists so the doc
    claims are real regression pins, not just prose:
      - claim 1: the bridge gate seam providers.base.dispatch_bridge + the real
        `manju bridge plan|run|adopt` commands;
      - claim 3: the adoption schema manju.qc.assurance/v1 is registered;
      - claim 5: the resolver functions resolve_tool / dry_run_tool.
    """
    from manju.providers import base as providers_base
    assert hasattr(providers_base, "dispatch_bridge"), (
        "claim 1: the bridge service seam dispatch_bridge is gone")

    top_leaf, groups = _registry_view()
    assert {"plan", "run", "adopt"} <= groups.get("bridge", set()), (
        "claim 1: the real `manju bridge plan|run|adopt` commands are gone")

    from manju.build import toolmap
    assert hasattr(toolmap, "resolve_tool") and hasattr(toolmap, "dry_run_tool"), (
        "claim 5: the whitelist RESOLVER functions resolve_tool/dry_run_tool are gone")

    from manju.core import contracts
    assert contracts.entry("manju.qc.assurance/v1")["status"] == "stable", (
        "claim 3: the adoption schema manju.qc.assurance/v1 must stay registered")

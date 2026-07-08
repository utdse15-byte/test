"""MCP tools (§11) — each a function returning a JSON-serializable dict.

Every tool is a thin call into the same core the CLI uses; there is no logic
here the CLI does not also have. The tool list and their JSON Schemas live in
one registry (:data:`TOOL_DEFS`) so ``tools/list`` and ``tools/call`` never
drift apart.

Safety rules baked in (§5, §10):
- ``update_shot`` is the ONLY tool that writes a shot file, and it runs a strict
  pipeline: parse → schema → locks-unchanged → lock-hash-verify → atomic write →
  re-check-and-restore-on-new-error.
- Locks cannot be added / removed / changed over MCP (that is human CLI work);
  the agent asks for a locked-content change via ``propose``.
- Dangerous commands (unlock / gc / pack / import) are intentionally ABSENT.

All paths returned to the client are project-relative (never absolute).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import ValidationError

from ..board.board import generate_board
from ..build.graph import redo_shot, run_build
from ..build.stale import evaluate_all
from ..build.status import project_status
from ..core.check import run_check
from ..core.container import Project
from ..core.events import append_event, tail_events
from ..core.locks import verify_locks
from ..core.models import ShotSpec
from ..core.yamlio import atomic_write_text, read_yaml, write_yaml
from ..exporters.jianying import export_jianying
from ..exporters.otio import export_otio
from ..exporters.srt_ass import export_captions
from ..qc.checks import run_qc
from ..qc.report import write_reports


class ToolError(RuntimeError):
    """A tool-level failure. The server reports it as ``isError: true`` with a
    ``{"error": <message>}`` payload (never a JSON-RPC-level error)."""


# --------------------------------------------------------------- helpers


def _coerce_locked(value: Any) -> dict[str, str]:
    """Normalize a ``locked`` field (dict, bare list, or absent) to a dict —
    the same coercion the model and check use, so comparisons are semantic."""
    if value is None:
        return {}
    if isinstance(value, list):
        return {str(p): "" for p in value}
    if isinstance(value, dict):
        return dict(value)
    return {}


def _slugify(title: str, maxlen: int = 40) -> str:
    """Lowercased slug that KEEPS CJK (``str.isalnum`` is true for it) and turns
    spaces/punctuation into ``_``; collapsed, stripped, capped at ``maxlen``."""
    chars = [c if c.isalnum() else "_" for c in title.strip().lower()]
    slug = re.sub(r"_+", "_", "".join(chars)).strip("_")
    if len(slug) > maxlen:
        slug = slug[:maxlen].strip("_")
    return slug or "proposal"


def _next_proposal_number(proposals_dir: Path) -> int:
    highest = 0
    if proposals_dir.exists():
        for f in proposals_dir.glob("*.md"):
            m = re.match(r"(\d+)", f.name)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1


def _claim_proposal_path(proposals_dir: Path, slug: str) -> tuple[int, Path]:
    """Atomically claim ``proposals/NNNN_<slug>.md`` (round W, #51).

    :func:`_next_proposal_number` is only a STARTING guess from a directory
    listing — two concurrent proposers (two agent sessions, or an agent
    racing a human's ``manju propose``) can compute the SAME "next" number
    from the same snapshot and then both write, one silently clobbering the
    other (worst case: same title → same slug → the identical path, a lost
    proposal; best case: same number, different slug → two files claiming
    one sequence number).

    The actual claim is an ``O_CREAT | O_EXCL`` create loop: whichever
    process wins the exclusive create for a given ``(number, slug)`` keeps
    it; every loser sees ``FileExistsError`` and retries at ``number + 1`` —
    so two concurrent proposers always end up on DISTINCT numbers, never a
    silent overwrite.

    Returns ``(number, path)`` with ``path`` already created as an EMPTY
    file that only WE just created — the caller fills it via
    :func:`~manju.core.yamlio.atomic_write_text`, an ``os.replace`` onto a
    path no concurrent claimant can also be holding.
    """
    proposals_dir.mkdir(parents=True, exist_ok=True)
    number = _next_proposal_number(proposals_dir)
    while True:
        path = proposals_dir / f"{number:04d}_{slug}.md"
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            number += 1
            continue
        os.close(fd)
        return number, path


# ------------------------------------------------------------- handlers


def _h_status(project: Project, args: dict) -> dict:
    return project_status(project)


def _h_explain(project: Project, args: dict) -> dict:
    from ..build.explain import explain

    return explain(project)


def _h_check(project: Project, args: dict) -> dict:
    return run_check(project).to_dict()


def _h_list_shots(project: Project, args: dict) -> dict:
    return {
        "shots": [
            {
                "id": st.shot_id,
                "state": st.state.value,
                "selected_take": st.selected_take,
                "note": st.note,
            }
            for st in evaluate_all(project)
        ]
    }


def _h_get_shot(project: Project, args: dict) -> dict:
    from ..core.writes import shot_text_hash

    shot_id = args["shot_id"]
    path = project.shot_path(shot_id)
    if not path.exists():
        raise ToolError(f"shot not found: {shot_id}")
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        data = {}
    # Round AA item 5 (#1): `rev` is the CAS token — pass it back as
    # update_shot's `expected_rev` to be refused (instead of silently
    # overwriting) if another entrance edits this shot before you save.
    return {"id": shot_id, "yaml": text, "data": data, "rev": shot_text_hash(project, shot_id)}


def _h_update_shot(project: Project, args: dict) -> dict:
    """The ONLY write-to-shot-file tool. Strict safety pipeline (§5)."""
    shot_id = args["shot_id"]
    yaml_content = args["yaml_content"]
    expected_rev = args.get("expected_rev")
    path = project.shot_path(shot_id)
    if not path.exists():
        raise ToolError(f"shot not found: {shot_id}")
    label = project.relpath(path)

    # (1) parse — must be a mapping
    try:
        new_data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise ToolError(f"invalid YAML: {exc}") from exc
    if not isinstance(new_data, dict):
        raise ToolError("yaml_content must parse to a mapping (a shot spec)")

    # (2) schema — ShotSpec.model_validate must pass (id defaulted like load_shot)
    validate_data = dict(new_data)
    validate_data.setdefault("id", shot_id)
    try:
        ShotSpec.model_validate(validate_data)
    except ValidationError as exc:
        raise ToolError(f"schema invalid: {exc}") from exc

    current_data = read_yaml(path) or {}
    if not isinstance(current_data, dict):
        current_data = {}
    current_locked = _coerce_locked(current_data.get("locked"))

    # (3) locks must be IDENTICAL — MCP clients may not add/remove/change locks
    if _coerce_locked(new_data.get("locked")) != current_locked:
        raise ToolError(
            "locks may not be added, removed, or changed over MCP — sealing/"
            "unsealing a lock is human CLI work (§5); write a proposal instead"
        )

    # (4) verify_locks: a locked field's value may not change over MCP
    violations = verify_locks(new_data, current_locked, label)
    if violations:
        raise ToolError(
            "locked field(s) would change (a locked field cannot be edited over "
            "MCP, §5): " + "; ".join(str(v) for v in violations)
        )

    # Round Z (agent ZA): the actual write+recheck is the cross-process-racy
    # part (§9) — wrapped in the same build lock every other mutating
    # entrance (build/redo/qc/select_take) takes, so this can no longer land
    # mid-build. Parsing/schema/lock-checks above are pure reads, held
    # outside the lock on purpose (never block a build over a request that
    # was going to be rejected anyway). ``BuildLocked`` deliberately
    # propagates uncaught, like ``_h_qc`` — the MCP server's own
    # ``tools/call`` dispatch already turns any raised exception into an
    # ``isError`` result (mcp/server.py), so a second local catch here would
    # only re-word the identical message, not change the outcome.
    from ..core.hashing import hash_text
    from ..runtime.buildlock import build_lock

    with build_lock(project.root, actor="ai"):
        # (6-pre) baseline check errors mentioning this shot's file
        original_text = path.read_text(encoding="utf-8")

        # Round AA item 5 (#1): optimistic concurrency (CAS). `expected_rev`
        # is the `rev` get_shot returned when the caller loaded this shot —
        # checked HERE, inside the lock and right before the write, so it
        # compares against the text this call is actually about to replace
        # (never a snapshot from before the lock-acquire race). `None` (an
        # agent that never called get_shot, or does not care) skips the
        # check, unchanged from before this round.
        if expected_rev is not None and hash_text(original_text) != expected_rev:
            raise ToolError(
                f"{shot_id}: 该镜头在你加载后已被其他入口修改(乐观锁校验失败)——"
                "请刷新后重试(重新调用 get_shot 获取最新 rev 再保存)"
            )

        before = run_check(project)
        before_errors = {e for e in before.errors if label in e}

        # (5) atomic write of the parsed dict
        write_yaml(path, new_data)

        # (6) re-check; restore on any NEW error mentioning this shot's file
        after = run_check(project)
        new_errors = [e for e in after.errors if label in e and e not in before_errors]
        if new_errors:
            atomic_write_text(path, original_text)  # roll back the edit
            return {
                "error": f"edit rejected: it introduces check errors in {label}",
                "check_errors": new_errors,
            }

    append_event(project.root, "ai", "mcp_update_shot", {"shot": shot_id})
    return {"ok": True, "check_warnings": after.warnings}


def _h_select_take(project: Project, args: dict) -> dict:
    """The checked selected_take write (round W, #39/#19): same pipeline as
    the CLI/board/GUI select — lock guard, post-write check scoped to this
    shot, revert on regression — through the ONE shared engine function so
    MCP can no longer write a project into a state the next `check` fails
    (round-V review finding #19). Takes the process build lock itself (the
    same shape as `_h_qc`): `select_take_checked` does not, so build's own
    auto-select (already inside its build_lock) never self-deadlocks."""
    from ..core.writes import WriteRejected, select_take_checked
    from ..runtime.buildlock import build_lock

    shot_id = args["shot_id"]
    take = args["take"]
    try:
        with build_lock(project.root, actor="ai"):
            result = select_take_checked(project, shot_id, take, actor="ai", via="mcp")
    except WriteRejected as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "shot": result["shot"], "take": result["take"]}


def _h_build(project: Project, args: dict) -> dict:
    return run_build(
        project,
        target=args.get("target", "final"),
        gen=args.get("gen", "missing"),
        regen_stale=bool(args.get("regen_stale", False)),
        dry_run=bool(args.get("dry_run", False)),
        actor="ai",
    ).to_dict()


def _h_redo(project: Project, args: dict) -> dict:
    takes = redo_shot(
        project,
        args["shot_id"],
        candidates=args.get("candidates"),
        provider=args.get("provider"),
        seed=args.get("seed"),
        actor="ai",
    )
    return {"takes": takes}


def _h_qc(project: Project, args: dict) -> dict:
    # qc writes qc.json/qc.md/repair_plan.yaml — a mutation. The process build
    # lock (R2) must cover qc from EVERY surface (review finding P1-2); the MCP
    # path held it nowhere before. Contention raises BuildLocked, rendered as
    # the tool's own one-line error by the server envelope.
    from ..runtime.buildlock import build_lock

    with build_lock(project.root, actor="ai"):
        return _h_qc_locked(project, args)


def _h_qc_locked(project: Project, args: dict) -> dict:
    report = run_qc(project, project.load_timeline(), deep=bool(args.get("deep", False)))
    paths = write_reports(project, report)
    return {
        "ok": report.ok,
        "items": [it.to_dict() for it in report.items],
        "reports": {k: project.relpath(v) for k, v in paths.items()},
    }


def _h_qc_brief(project: Project, args: dict) -> dict:
    """Round V (§6): the review package a vision-capable agent consumes — frames
    + shot context + the visual-qc-review criteria pointer + the verdict shape.
    Round X (agent XB): `mode="consistency"` briefs cross-shot comparison units
    (character/pair/scene contact sheets) instead of per-shot rows."""
    from ..qc.agent_review import qc_brief

    shots = args.get("shots")
    if shots is not None and not isinstance(shots, list):
        raise ToolError("shots must be an array of shot ids")
    mode = str(args.get("mode") or "shots")
    try:
        return qc_brief(project, [str(s) for s in shots] if shots else None, mode=mode)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _h_qc_coverage(project: Project, args: dict) -> dict:
    """Round X (agent XB, user pain #2): per-shot and per-consistency-unit
    AI-judgment coverage — reviewed / stale / never, plus a summary with a
    total `gaps` count (never-reviewed shots + units)."""
    from ..qc.agent_review import qc_coverage

    return qc_coverage(project)


def _h_qc_verdict(project: Project, args: dict) -> dict:
    """Round V (§6): intake the agent's structured verdicts, bind each to the
    take bytes it judged, append to reports/qc_agent.jsonl. `run_qc` folds them
    back in as [AI判读] items on the next pass."""
    from ..qc.agent_review import VerdictError, record_verdicts

    payload = args
    if "verdicts" not in args and isinstance(args.get("from_file"), str):
        import json as _json

        try:
            payload = _json.loads(project.resolve(args["from_file"]).read_text(encoding="utf-8"))
        except Exception as exc:
            raise ToolError(f"could not read verdict file: {exc}") from exc
    try:
        return record_verdicts(project, payload, actor="ai")
    except VerdictError as exc:
        raise ToolError(str(exc)) from exc


def _h_export(project: Project, args: dict) -> dict:
    # Round Z (agent ZA): srt/ass land in captions/ — the SAME files a
    # concurrent build's own captions phase writes (§9) — so this needs the
    # cross-process build lock too, mirroring qc/select_take. BuildLocked
    # propagates uncaught (see _h_update_shot's note above).
    from ..runtime.buildlock import build_lock

    formats = args.get("formats") or []
    if not isinstance(formats, list) or not formats:
        raise ToolError("formats must be a non-empty array of srt|otio|jianying")
    with build_lock(project.root, actor="ai"):
        timeline = project.load_timeline()
        if timeline is None:
            raise ToolError("no timeline.json — run the build tool first")
        outputs: dict[str, str] = {}
        for fmt in formats:
            if fmt == "srt":
                paths = export_captions(project, timeline)
                outputs["srt"] = project.relpath(paths["srt"])
                outputs["ass"] = project.relpath(paths["ass"])
            elif fmt == "otio":
                outputs["otio"] = project.relpath(export_otio(project, timeline))
            elif fmt == "jianying":
                outputs["jianying"] = project.relpath(export_jianying(project, timeline))
            else:
                raise ToolError(f"unknown export format: {fmt!r} (use srt|otio|jianying)")
    return {"outputs": outputs}


def _h_events(project: Project, args: dict) -> dict:
    n = args.get("n", 20)
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 20
    return {"events": tail_events(project.root, n)}


def _h_board(project: Project, args: dict) -> dict:
    return {"path": project.relpath(generate_board(project))}


def _h_propose(project: Project, args: dict) -> dict:
    """The agent's legitimate channel for locked-content change requests (§5).
    Numbering is an atomic claim (#51) — two concurrent proposers never
    collide on the same file."""
    title = args["title"]
    body = args["body"]
    _number, path = _claim_proposal_path(project.proposals_dir, _slugify(title))
    atomic_write_text(path, f"# {title}\n\n{body}")
    rel = project.relpath(path)
    append_event(project.root, "ai", "propose", {"path": rel, "title": title})
    return {"path": rel}


# ------------------------------------------------- director loop (goal 17)
# The six-step collaboration contract, driven end-to-end over one audited
# surface. propose/confirm/execute are DELIBERATELY three separate tools — an
# agent must relay the impact + cost to the human and get a confirm before any
# spend (§8.3 approve-before-execute). The paid steps' assume_yes comes ONLY
# from the confirmed proposal, enforced engine-side at execute time too.


def _h_director_propose(project: Project, args: dict) -> dict:
    from ..build.director import DirectorError, propose

    actions = args.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ToolError("actions must be a non-empty array of whitelisted action objects")
    try:
        proposal = propose(project, actions, why=str(args.get("why") or ""), actor="ai")
    except DirectorError as exc:
        raise ToolError(str(exc)) from exc
    return proposal.model_dump()


def _h_director_confirm(project: Project, args: dict) -> dict:
    from ..build.director import DirectorError, confirm

    try:
        return confirm(project, str(args["id"]), actor="ai").model_dump()
    except DirectorError as exc:
        raise ToolError(str(exc)) from exc


def _h_director_execute(project: Project, args: dict) -> dict:
    from ..build.director import DirectorError, execute

    try:
        return execute(project, str(args["id"]), actor="ai").to_dict()
    except DirectorError as exc:
        raise ToolError(str(exc)) from exc


def _h_director_suggest(project: Project, args: dict) -> dict:
    from ..build.director import suggest_next

    return {"suggestions": [s.to_dict() for s in suggest_next(project)]}


def _h_funnel_status(project: Project, args: dict) -> dict:
    """Read-only creation-funnel status (round V, goal item 2)."""
    from ..build.funnel import funnel_status

    return funnel_status(project)


# --------------------------------------------------------------- registry

_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def _h_skill_list(project: Project, args: dict) -> dict:
    from ..core.skills import core_skill_shadow_warning, list_skills

    return {
        "skills": [s.to_dict() for s in list_skills(project)],
        # goal item 46: a loud warning when the project tried to shadow the
        # core protocol skill (id "manju") — the shadow is already ignored by
        # list_skills; this makes the ignoring visible to the MCP caller too.
        "core_skill_shadow_warning": core_skill_shadow_warning(project),
    }


def _h_skill_show(project: Project, args: dict) -> dict:
    from ..core.skills import load_skill

    skill_id = str(args.get("id") or "")
    try:
        info = load_skill(project, skill_id)
    except KeyError as exc:
        raise ToolError(str(exc)) from exc
    text = info.path.read_text(encoding="utf-8") if info.path else ""
    # round AA (goal item 8): content served over MCP is the same usage
    # signal core/evaluate.py reports on — best-effort, never blocks the show.
    try:
        append_event(project.root, "ai", "skill_used", {"skill": skill_id, "via": "mcp"})
    except Exception:
        pass
    return {"skill": info.to_dict(), "text": text}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


Handler = Callable[[Project, dict], dict]

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "status",
        "description": "Project status snapshot — project/preset/mode, per-state shot "
        "counts, timeline, latest final, QC summary, spend, and a suggested next step (§10).",
        "inputSchema": _EMPTY_SCHEMA,
        "handler": _h_status,
    },
    {
        "name": "explain",
        "description": "Why will the next build do what it will do? Read-only: "
        "per-shot picture/voice states with hash evidence, timeline fingerprint "
        "diff, final/proxy content-key verdicts. Never mutates, never spends.",
        "inputSchema": _EMPTY_SCHEMA,
        "handler": _h_explain,
    },
    {
        "name": "check",
        "description": "Run the safety net: schema + referential integrity + hard "
        "lock verification + secret scan. Returns {ok, errors, warnings} (§4, §5).",
        "inputSchema": _EMPTY_SCHEMA,
        "handler": _h_check,
    },
    {
        "name": "list_shots",
        "description": "List every shot with its build state (missing/fresh/stale/"
        "manual/needs_selection/broken), selected take, and a note.",
        "inputSchema": _EMPTY_SCHEMA,
        "handler": _h_list_shots,
    },
    {
        "name": "get_shot",
        "description": "Read one shot file: raw YAML text plus the parsed mapping, "
        "plus `rev` — a content hash. Pass `rev` back as update_shot's `expected_rev` "
        "for optimistic-concurrency (CAS) protection against a stale overwrite.",
        "inputSchema": _schema(
            {"shot_id": {"type": "string", "description": "e.g. S002"}},
            ["shot_id"],
        ),
        "handler": _h_get_shot,
    },
    {
        "name": "update_shot",
        "description": "The ONLY tool that writes a shot file. Validates schema, "
        "refuses to add/remove/change locks or edit a locked field (§5), writes "
        "atomically, then re-checks and rolls back if the edit breaks the shot.",
        "inputSchema": _schema(
            {
                "shot_id": {"type": "string"},
                "yaml_content": {
                    "type": "string",
                    "description": "Full replacement YAML for the shot file.",
                },
                "expected_rev": {
                    "type": "string",
                    "description": "Optional optimistic-concurrency (CAS) token: "
                    "the `rev` get_shot returned when you loaded this shot. If the "
                    "shot changed since (another entrance wrote it), the write is "
                    "refused instead of silently overwriting — call get_shot again "
                    "for the current rev and retry. Omit to skip the check.",
                },
            },
            ["shot_id", "yaml_content"],
        ),
        "handler": _h_update_shot,
    },
    {
        "name": "select_take",
        "description": "Set a shot's selected_take (the take must already exist). "
        "The decision is one line of text truth (§3).",
        "inputSchema": _schema(
            {"shot_id": {"type": "string"}, "take": {"type": "string"}},
            ["shot_id", "take"],
        ),
        "handler": _h_select_take,
    },
    {
        "name": "build",
        "description": "One-command build: fill gaps → compile timeline → render → "
        "QC → exports. Never overturns an existing selection (§4.3, §11).",
        "inputSchema": _schema(
            {
                "target": {
                    "type": "string",
                    "enum": ["proxy", "final", "exports", "qc"],
                    "default": "final",
                    "description": "qc does NOT render first — it QCs the "
                    "newest EXISTING renders/final/*.mp4 against a freshly "
                    "recompiled timeline; use target=final beforehand for QC "
                    "on a fresh render. The result names the checked artifact "
                    "as qc_final.",
                },
                "gen": {
                    "type": "string",
                    "enum": ["missing", "auto", "off"],
                    "default": "missing",
                },
                "regen_stale": {"type": "boolean", "default": False},
                "dry_run": {"type": "boolean", "default": False},
            }
        ),
        "handler": _h_build,
    },
    {
        "name": "redo",
        "description": "Force new takes for one shot (append-only; an existing "
        "selection stands unless the shot had none).",
        "inputSchema": _schema(
            {
                "shot_id": {"type": "string"},
                "candidates": {"type": "integer"},
                "provider": {"type": "string"},
                "seed": {"type": "integer"},
            },
            ["shot_id"],
        ),
        "handler": _h_redo,
    },
    {
        "name": "qc",
        "description": "Three-layer QC over the compiled timeline; writes qc.json, "
        "qc.md and repair_plan.yaml. Returns {ok, items, reports} (§9).",
        "inputSchema": _schema({"deep": {"type": "boolean", "default": False}}),
        "handler": _h_qc,
    },
    {
        "name": "qc_brief",
        "description": "Round V visual-QC (§6): package review frames (mid + "
        "first/last of the selected take) + shot context (scene, characters with "
        "their bible ref images, must_show/avoid, continuity locks, dialogue) + a "
        "pointer to the visual-qc-review skill (the A–J criteria) + the verdict "
        "JSON shape — for a VISION-CAPABLE agent to judge with its own eyes. "
        "Manju runs NO vision model. `shots` scopes it; omit for every reviewable "
        "shot. Then read `manju skills show visual-qc-review` and return verdicts "
        "via qc_verdict. Round X (agent XB, user pain #2): `mode=consistency` "
        "briefs CROSS-shot comparison units instead of per-shot rows — one "
        "contact-sheet image per character appearing in >1 shot (bible ref + "
        "one take frame per appearance, identity/outfit drift), one side-by-side "
        "pair board per adjacent shot pair sharing a scene, and one contact "
        "sheet per scene (scene/lighting continuity). Return verdicts with "
        "`unit` (not `shot`) via qc_verdict; each binds to ALL member take "
        "hashes at once.",
        "inputSchema": _schema(
            {
                "shots": {"type": "array", "items": {"type": "string"},
                         "description": "shot ids to brief; omit for all. In "
                         "consistency mode, keeps units with >=1 matching member"},
                "mode": {"type": "string", "enum": ["shots", "consistency"],
                         "default": "shots",
                         "description": "shots = per-shot rows (default); "
                         "consistency = cross-shot comparison units"},
            }
        ),
        "handler": _h_qc_brief,
    },
    {
        "name": "qc_coverage",
        "description": "Round X (agent XB, user pain #2): per-shot AND "
        "per-consistency-unit AI-judgment coverage — {shots: {id: state}, "
        "units: {id: {state, kind, label}}, summary: {..., gaps}}, state is "
        "reviewed (a verdict's bound bytes match the CURRENT take(s)) / stale "
        "(a verdict exists but bytes moved) / never (no verdict was ever "
        "recorded). `summary.gaps` is the never-reviewed count `run_qc` also "
        "surfaces as one info item.",
        "inputSchema": _EMPTY_SCHEMA,
        "handler": _h_qc_coverage,
    },
    {
        "name": "qc_verdict",
        "description": "Round V visual-QC (§6): intake the agent's structured "
        "verdicts and append them to reports/qc_agent.jsonl, each BOUND to the "
        "take bytes it judged. `verdicts` is an array of "
        "{shot, take, criterion, level(blocker|issue|fyi), message(中文), "
        "evidence, frame_ms?}. Next `qc` pass surfaces matching verdicts as "
        "[AI判读] items (blocker→error/issue→warn/fyi→info); a regenerated take "
        "makes its old verdicts stale. Unknown shot is rejected. Round X (agent "
        "XB): a verdict may instead carry `unit` (a comparison-unit id from a "
        "`qc_brief mode=consistency` response) — it binds to ALL of that unit's "
        "member take hashes at once. Instead of `verdicts` inline, pass "
        "`from_file` (a project-relative path to a JSON file holding the same "
        "payload).",
        "inputSchema": _schema(
            {
                "verdicts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "verdict objects; each needs (shot or unit) + level",
                },
                "from_file": {
                    "type": "string",
                    "description": "project-relative path to a JSON verdicts file "
                    "(alternative to inline `verdicts`)",
                },
            }
        ),
        "handler": _h_qc_verdict,
    },
    {
        "name": "export",
        "description": "Export from the compiled timeline. Requires timeline.json. "
        "Returns {outputs: {format: relpath}}.",
        "inputSchema": _schema(
            {
                "formats": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["srt", "otio", "jianying"]},
                    "minItems": 1,
                }
            },
            ["formats"],
        ),
        "handler": _h_export,
    },
    {
        "name": "events",
        "description": "Tail the collaboration log (who did what, when — §10).",
        "inputSchema": _schema({"n": {"type": "integer", "default": 20}}),
        "handler": _h_events,
    },
    {
        "name": "board",
        "description": "Generate the static HTML review board and return its path.",
        "inputSchema": _EMPTY_SCHEMA,
        "handler": _h_board,
    },
    {
        "name": "propose",
        "description": "Write a proposal to proposals/ — the agent's legitimate "
        "channel to request a locked-content change (§5). Returns {path}.",
        "inputSchema": _schema(
            {"title": {"type": "string"}, "body": {"type": "string"}},
            ["title", "body"],
        ),
        "handler": _h_propose,
    },
    {
        "name": "director_propose",
        "description": "PROPOSE a plan for the AI-director loop (goal 17, step 1). "
        "`actions` is an array of whitelisted action objects (each has a `type`: "
        "build|redo|voice|repair|mixer|captions|packaging|snapshot|rollback), each "
        "1:1 with an engine entry point. Each is shape-validated and annotated with "
        "its impact (affected shots/outputs) and est cost (the SAME dry-run "
        "estimators). Persists reports/proposals/<id>.yaml and returns the full "
        "proposal. Nothing runs and nothing is spent — relay the cost to the human, "
        "then confirm. Example action: {\"type\":\"redo\",\"shot\":\"S002\"}.",
        "inputSchema": _schema(
            {
                "actions": {
                    "type": "array",
                    "items": {"type": "object"},
                    "minItems": 1,
                    "description": "whitelisted action objects, each with a 'type'",
                },
                "why": {"type": "string", "description": "one line: why this plan"},
            },
            ["actions"],
        ),
        "handler": _h_director_propose,
    },
    {
        "name": "director_confirm",
        "description": "CONFIRM a proposal (goal 17, step 3) — the explicit, "
        "separate approve-before-execute gate. NEVER call this without first "
        "relaying the proposal's impact + est cost to the human and getting their "
        "yes (§8.3). A proposal whose project changed since it was proposed is "
        "refused as 待更新/expired. Returns the updated proposal.",
        "inputSchema": _schema(
            {"id": {"type": "string", "description": "proposal id, e.g. prop_0001"}},
            ["id"],
        ),
        "handler": _h_director_confirm,
    },
    {
        "name": "director_execute",
        "description": "EXECUTE a CONFIRMED proposal (goal 17, steps 4-6): runs its "
        "actions in order through the real engine, auto-snapshots BEFORE mutating "
        "(so rollback is one step), stops at the first failure, and returns the "
        "structured diff (truth text + finals) plus next-step suggestions. Paid "
        "steps ride the confirmed proposal's assume_yes — the spend gate still "
        "applies engine-side (defense in depth). Only a confirmed, current "
        "proposal executes.",
        "inputSchema": _schema(
            {"id": {"type": "string", "description": "proposal id, e.g. prop_0001"}},
            ["id"],
        ),
        "handler": _h_director_execute,
    },
    {
        "name": "director_suggest",
        "description": "SUGGEST NEXT (goal 17, step 6, standalone): deterministic "
        "next-step nudges from the existing signals (funnel-first→write, "
        "missing→generate, stale→redo, needs_selection→select, QC→repair, "
        "budget→remind). MOST carry a ready-made action payload you can pass "
        "straight to director_propose; some (funnel/select/budget) are "
        "advisory-only with `action: null`. Read-only.",
        "inputSchema": _EMPTY_SCHEMA,
        "handler": _h_director_suggest,
    },
    {
        "name": "funnel_status",
        "description": "创作漏斗状态 (round V, goal item 2): the staged creation "
        "workflow as data — 立意→梗概→节拍→剧本→分镜→生成计划→生成. Per-stage "
        "{id, cn(中文名), state(done|current|todo), artifact, evidence, skill, "
        "next_action}; the first not-done stage is `current`. Read-only: it "
        "detects progress from files on disk, scaffolds nothing and spends "
        "nothing. Use it to know what to write next before generating.",
        "inputSchema": _EMPTY_SCHEMA,
        "handler": _h_funnel_status,
    },
    {
        "name": "skill_list",
        "description": "技能库索引 (round V): every visible skill (project > user "
        "> bundled) with id, 何时用 one-liner, tags. Progressive disclosure: read "
        "this index cheaply, then pull ONE skill's full text via skill_show — "
        "never inline the whole library.",
        "inputSchema": _EMPTY_SCHEMA,
        "handler": _h_skill_list,
    },
    {
        "name": "skill_show",
        "description": "One skill's FULL SKILL.md text by id (project > user > "
        "bundled resolution). Load on demand when the index says it applies to "
        "the task at hand.",
        "inputSchema": _schema({"id": {"type": "string"}}, ["id"]),
        "handler": _h_skill_show,
    },
]

TOOLS: dict[str, dict[str, Any]] = {t["name"]: t for t in TOOL_DEFS}


def list_tools() -> list[dict[str, Any]]:
    """The ``tools/list`` payload: name + description + inputSchema only."""
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in TOOL_DEFS
    ]


def call_tool(project: Project, name: str, arguments: dict | None) -> dict:
    """Dispatch a ``tools/call``. Raises :class:`ToolError` for an unknown tool
    (the server turns any raised exception into an ``isError`` result)."""
    entry = TOOLS.get(name)
    if entry is None:
        raise ToolError(f"unknown tool: {name!r}")
    return entry["handler"](project, arguments or {})

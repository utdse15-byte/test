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


# ------------------------------------------------------------- handlers


def _h_status(project: Project, args: dict) -> dict:
    return project_status(project)


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
    shot_id = args["shot_id"]
    path = project.shot_path(shot_id)
    if not path.exists():
        raise ToolError(f"shot not found: {shot_id}")
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        data = {}
    return {"id": shot_id, "yaml": text, "data": data}


def _h_update_shot(project: Project, args: dict) -> dict:
    """The ONLY write-to-shot-file tool. Strict safety pipeline (§5)."""
    shot_id = args["shot_id"]
    yaml_content = args["yaml_content"]
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

    # (6-pre) baseline check errors mentioning this shot's file
    original_text = path.read_text(encoding="utf-8")
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
    shot_id = args["shot_id"]
    take = args["take"]
    if project.get_take(shot_id, take) is None:
        raise ToolError(f"{shot_id} has no take '{take}'")
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take),
    )
    append_event(project.root, "ai", "select", {"shot": shot_id, "take": take})
    return {"ok": True}


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
    report = run_qc(project, project.load_timeline(), deep=bool(args.get("deep", False)))
    paths = write_reports(project, report)
    return {
        "ok": report.ok,
        "items": [it.to_dict() for it in report.items],
        "reports": {k: project.relpath(v) for k, v in paths.items()},
    }


def _h_export(project: Project, args: dict) -> dict:
    formats = args.get("formats") or []
    if not isinstance(formats, list) or not formats:
        raise ToolError("formats must be a non-empty array of srt|otio|jianying")
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
    """The agent's legitimate channel for locked-content change requests (§5)."""
    title = args["title"]
    body = args["body"]
    project.proposals_dir.mkdir(parents=True, exist_ok=True)
    number = _next_proposal_number(project.proposals_dir)
    path = project.proposals_dir / f"{number:04d}_{_slugify(title)}.md"
    atomic_write_text(path, f"# {title}\n\n{body}")
    rel = project.relpath(path)
    append_event(project.root, "ai", "propose", {"path": rel, "title": title})
    return {"path": rel}


# --------------------------------------------------------------- registry

_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


Handler = Callable[[Project, dict], dict]

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "status",
        "description": "Project status snapshot — phase, per-state shot counts, "
        "timeline, latest final, QC summary, spend, and a suggested next step (§10).",
        "inputSchema": _EMPTY_SCHEMA,
        "handler": _h_status,
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
        "description": "Read one shot file: raw YAML text plus the parsed mapping.",
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

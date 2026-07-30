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
from ..build.graph import WaitingUser, redo_shot, run_build
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
from . import policy as _P


class ToolError(RuntimeError):
    """A tool-level failure. The server reports it as ``isError: true`` with a
    ``{"error": <message>, "code": <token>}`` payload (never a JSON-RPC-level
    error). ``code`` (round-2 UX audit) is the stable machine token an agent
    can branch on — the same contract the CLI's ``_fail`` documents; the
    default ``"error"`` keeps every existing raise byte-compatible. ``payload``
    (optional) is a FULL structured body for errors that carry evidence
    (e.g. update_shot's rollback ships its check_errors), mirroring
    :class:`AgentProfileDenied`."""

    def __init__(self, message: str, *, code: str = "error",
                 payload: dict | None = None):
        super().__init__(message)
        self.code = code
        self.payload = payload


class AgentProfileDenied(ToolError):
    """A call refused by the active agent profile (§ DR05 ruling 4). Carries the
    structured ``agent_profile_denied`` payload so the server renders it as a
    structured ``isError`` result — never a natural-language-only error, never
    disguised as unknown-tool. Enforcement is at call dispatch; the engine guards
    (locks, CAS, build lock, spend gate, director confirm) still apply on top."""

    def __init__(self, payload: dict):
        super().__init__(payload.get("error", "agent_profile_denied"))
        self.payload = payload


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

    info = explain(project)
    # DR03B parity: the CLI `explain --graph` flag has a shared service twin here
    # (same diagnose_project()); default (no arg) is byte-identical to before.
    if args.get("graph"):
        from ..build.graphdiag import diagnose_project

        info["graph"] = diagnose_project(project)
    return info


def _h_impact(project: Project, args: dict) -> dict:
    """Read-only interconnection report (WP1). Mirrors ``manju impact``."""
    from ..build.impact import impact_report

    shot_id = str(args.get("shot_id") or args.get("shot") or "").strip()
    if not shot_id:
        raise ToolError("impact requires shot_id")
    field = args.get("field")
    value = args.get("value")
    if field is not None:
        field = str(field)
    if value is not None:
        value = str(value)
    try:
        return impact_report(project, shot_id, field=field, new_value=value)
    except Exception as exc:
        raise ToolError(" ".join(str(exc).split())[:500]) from exc


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

    # (1) parse — must be a mapping. A non-string yaml_content (a JSON object or
    # number the wire can carry for the declared "string" field — e.g. an agent
    # round-tripping get_shot's dict) makes yaml.safe_load call stream.read() and
    # raise AttributeError, which `except yaml.YAMLError` does NOT catch — so it
    # leaked as a raw internal error instead of a structured ToolError.
    if not isinstance(yaml_content, str):
        raise ToolError(
            "yaml_content must be a string (the shot spec as YAML/JSON text)",
            code="invalid_argument")
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

    # Early lock pre-check (outside build_lock): reject obvious violations
    # without blocking a build. The authoritative re-check runs INSIDE the
    # lock against live disk (P0-1) so a human seal between pre-check and
    # write cannot be erased.
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
        # TRISURFACE F-18: `locked_field` — the branchable answer is "write a
        # proposal", and `code:"error"` hid that from every agent.
        raise ToolError(
            "locked field(s) would change (a locked field cannot be edited over "
            "MCP, §5): " + "; ".join(str(v) for v in violations),
            code="locked_field",
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
                "请刷新后重试(重新调用 get_shot 获取最新 rev 再保存)",
                code="rev_conflict",  # TRISURFACE F-18: branch = re-get_shot
            )

        # P0-1: re-validate §5 locks against LIVE disk inside the lock.
        live = read_yaml(path) or {}
        if not isinstance(live, dict):
            live = {}
        live_locked = _coerce_locked(live.get("locked"))
        if _coerce_locked(new_data.get("locked")) != live_locked:
            raise ToolError(
                "locks may not be added, removed, or changed over MCP — sealing/"
                "unsealing a lock is human CLI work (§5); write a proposal instead"
                " (live lock map changed since pre-check)"
            )
        live_violations = verify_locks(new_data, live_locked, label)
        if live_violations:
            raise ToolError(
                "locked field(s) would change (a locked field cannot be edited over "
                "MCP, §5): " + "; ".join(str(v) for v in live_violations),
                code="locked_field",
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
            # Round-2 UX audit: RETURNING this dict stamped isError:false —
            # an agent branching on the MCP-spec signal concluded the write
            # landed when it was rolled back. A refusal RAISES, like every
            # other refusal on this surface (locks, CAS, schema).
            msg = f"edit rejected: it introduces check errors in {label}"
            raise ToolError(msg, code="check_rejected",
                            payload={"error": msg, "check_errors": new_errors,
                                     "code": "check_rejected"})

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


def _h_build(project: Project, args: dict, *, profile: str = _P.COLLABORATIVE) -> dict:
    # Round-2 UX audit (paid-safety): an unknown target fell through every
    # phase branch — generation (spend=POSSIBLE) still ran and the requested
    # phase was silently skipped. The CLI has guarded this exact hole forever;
    # MCP was the one unguarded entrance. Validate against the tool's OWN
    # advertised (byte-pinned) enum so wire contract and enforcement can
    # never drift apart.
    target = args.get("target", "final")
    allowed = TOOLS["build"]["inputSchema"]["properties"]["target"]["enum"]
    if target not in allowed:
        raise ToolError(
            f"unknown target: {target!r} — MCP build accepts {allowed}",
            code="invalid_argument")
    lang = args.get("lang")
    if lang is not None and str(lang).strip():
        # C44: validate before run_build so bad lang is invalid_argument, not
        # a mid-phase BuildError after possible spend planning.
        from ..core.locale import validate_lang

        try:
            lang = validate_lang(str(lang).strip())
        except Exception as exc:
            raise ToolError(" ".join(str(exc).split()), code="invalid_argument") from exc
    else:
        lang = None
    try:
        return run_build(
            project,
            target=target,
            gen=args.get("gen", "missing"),
            regen_stale=bool(args.get("regen_stale", False)),
            dry_run=bool(args.get("dry_run", False)),
            actor="ai",
            agent_profile=profile,  # AI_IDE_16 §10 keyframe spend gate
            lang=lang,
            # C60: spend gate confirm (parity with GUI / redo). Fail-CLOSED
            # `is True` (not bool()): a JSON string "false"/"0"/"no" is truthy,
            # so bool() would silently APPROVE a paid provider the caller never
            # confirmed. Only a real JSON true confirms — the same idiom
            # policy.decide() uses for dry_run (UNKNOWN never guessed into PASS).
            assume_yes=args.get("assume_yes") is True,
        ).to_dict()
    except Exception as exc:
        # P1 item 5: adapt via the shared classifier instead of re-deriving the
        # exception→code mapping here. WaitingUser → waiting_user (C61: agents
        # re-call with assume_yes); BuildCanceled/ProviderCanceled → canceled
        # (C90: cooperative mid-build cancel). Anything the classifier calls
        # FAILED (an unknown/real error) propagates UNCHANGED — it must not be
        # silently reshaped into a soft ToolError.
        from ..core.outcomes import OutcomeCode, classify_exception

        outcome = classify_exception(exc)
        if outcome.code in (OutcomeCode.WAITING_USER, OutcomeCode.CANCELED):
            raise ToolError(outcome.message, code=str(outcome.code)) from exc
        raise


def _h_redo(project: Project, args: dict) -> dict:
    # C59: assume_yes must reach spend_gate — agents otherwise always hit
    # WaitingUser on priced providers with no way to confirm mid-tool.
    try:
        takes = redo_shot(
            project,
            args["shot_id"],
            candidates=args.get("candidates"),
            provider=args.get("provider"),
            seed=args.get("seed"),
            actor="ai",
            # fail-CLOSED `is True` (see _h_build): a truthy non-bool argument
            # (JSON "false"/"0"/"no") must NOT approve the paid spend gate.
            assume_yes=args.get("assume_yes") is True,
        )
    except WaitingUser as exc:
        # C61: same structured gate as build.
        raise ToolError(
            " ".join(str(exc).split())[:500],
            code="waiting_user",
        ) from exc
    except Exception as exc:
        # C90: mid-poll cancel during redo.
        from ..providers.base import ProviderCanceled
        if isinstance(exc, ProviderCanceled):
            raise ToolError(
                " ".join(str(exc).split())[:500],
                code="canceled",
            ) from exc
        raise
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
    # C3/C6: optional final_path / lang so agent QC hits locale film.
    # Contain final_path under project root; never fall back to base final when
    # lang is set; use locale overlay timeline for parity with graph.
    from pathlib import Path

    final_path = args.get("final_path")
    lang = args.get("lang")
    timeline = project.load_timeline()
    fp = None
    if final_path:
        from ..core.container import ProjectError as _PE

        try:
            fp = project.resolve(str(final_path))
        except _PE as exc:
            raise ToolError(str(exc), code="out_of_project") from exc
        except Exception as exc:
            raise ToolError(f"final_path 无效: {final_path} ({exc})") from exc
    elif lang:
        from ..build.locale_build import apply_locale_overlay, newest_locale_final
        from ..core.locale import validate_lang

        try:
            lang = validate_lang(str(lang))
        except Exception as exc:
            raise ToolError(str(exc)) from exc
        fp = newest_locale_final(project, lang)
        if fp is None:
            raise ToolError(
                f"locale {lang}: 无 locale 成片可 QC"
                f"(renders/final/locales/{lang}/)—"
                "不会回退 base final",
                code="no_locale_final",
            )
        if timeline is not None:
            try:
                timeline = apply_locale_overlay(project, timeline, lang)
            except Exception:
                pass  # overlay best-effort; final_path still locale
    report = run_qc(
        project, timeline, deep=bool(args.get("deep", False)),
        final_path=fp,
    )
    # DR02 WP4: derive the read-only assurance block exactly as the CLI does and
    # thread it into the reports + result. Separate axis from `ok` (never changes
    # it); degrade gracefully — any failure simply omits the block.
    assurance = None
    try:
        from ..qc.assurance import assurance_for_all

        assurance = assurance_for_all(project, qc_report=report)
    except Exception:
        assurance = None
    paths = write_reports(project, report, assurance=assurance)
    result = {
        "ok": report.ok,
        "items": [it.to_dict() for it in report.items],
        "reports": {k: project.relpath(v) for k, v in paths.items()},
    }
    if assurance is not None:
        from ..qc.report import build_assurance_block

        result["assurance"] = build_assurance_block(project, assurance)
    return result


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
    from ..build.graph import WaitingUser, final_export_gate
    from ..runtime.buildlock import build_lock

    formats = args.get("formats") or []
    if not isinstance(formats, list) or not formats:
        raise ToolError("formats must be a non-empty array of "
                        "srt|vtt|ttml|otio|edl|fcpxml|xmeml|jianying|capcut")
    # TRISURFACE F-01: the final_export ask_before token binds THIS surface
    # too — before, only the CLI enforced it while SKILL.md §5 told agents the
    # token means "stop and ask". Same structured waiting_user as build/redo
    # (C61), same fail-closed confirm idiom (C59/C60: only a real JSON true).
    try:
        final_export_gate(
            project, confirmed=args.get("assume_yes") is True, noun="导出",
            retry="re-call export with assume_yes: true after the human confirms")
    except WaitingUser as exc:
        raise ToolError(" ".join(str(exc).split())[:500],
                        code="waiting_user") from exc
    with build_lock(project.root, actor="ai"):
        timeline = project.load_timeline()
        if timeline is None:
            raise ToolError("no timeline.json — run the build tool first")
        outputs: dict[str, str] = {}
        for fmt in formats:
            if fmt in ("srt", "vtt"):
                # one writer, three sibling caption files (srt/ass/vtt) —
                # report all three whichever alias the caller named.
                paths = export_captions(project, timeline)
                for k in ("srt", "ass", "vtt"):
                    outputs[k] = project.relpath(paths[k])
            elif fmt == "otio":
                outputs["otio"] = project.relpath(export_otio(project, timeline))
            elif fmt == "jianying":
                outputs["jianying"] = project.relpath(export_jianying(project, timeline))
            # TRISURFACE F-19: the CLI exports nine-plus formats; this tool
            # accepted three and told agents "use srt|otio|jianying" — the
            # skill teaches the full set, so agents dead-ended on the rest.
            # Same engine calls the CLI makes, same build-lock scope.
            elif fmt == "ttml":
                from ..exporters.ttml import export_ttml

                outputs["ttml"] = project.relpath(export_ttml(project, timeline))
            elif fmt == "edl":
                from ..exporters.edl import export_edl

                outputs["edl"] = project.relpath(export_edl(project, timeline))
            elif fmt == "fcpxml":
                from ..exporters.fcpxml import export_fcpxml

                outputs["fcpxml"] = project.relpath(export_fcpxml(project, timeline))
            elif fmt == "xmeml":
                from ..exporters.xmeml import export_xmeml

                outputs["xmeml"] = project.relpath(export_xmeml(project, timeline))
            elif fmt == "capcut":
                from ..exporters.native_draft import (
                    ExporterUnavailable,
                    export_capcut_native,
                )

                try:
                    outputs["capcut"] = project.relpath(
                        export_capcut_native(project, timeline))
                except ExporterUnavailable as exc:
                    raise ToolError(" ".join(str(exc).split())[:300]) from exc
            else:
                raise ToolError(
                    f"unknown export format: {fmt!r} (use srt|vtt|ttml|otio|edl|"
                    "fcpxml|xmeml|jianying|capcut)")
    return {"outputs": outputs}


def _h_events(project: Project, args: dict) -> dict:
    n = args.get("n", 20)
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 20
    # Round-2 UX audit: n=0 dumped the ENTIRE unbounded log into one content
    # text and a negative n hit the tail quirk. Clamp HERE only — the core
    # tail_events semantics are pinned where they live (test_fp_events_tail).
    if n <= 0:
        n = 20
    n = min(n, 1000)
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


def _h_director_execute(project: Project, args: dict, *,
                        profile: str = _P.COLLABORATIVE) -> dict:
    from ..build.director import DirectorError, UnknownProposal, execute

    try:
        # AI_IDE_16 §10: the confirmed-proposal build honors the keyframe spend
        # gate under the unattended profile (the ENGINE gate; the human confirm
        # released the SPEND, the ladder is a separate discipline).
        return execute(project, str(args["id"]), actor="ai",
                       agent_profile=profile).to_dict()
    except UnknownProposal as exc:
        # TRISURFACE F-18: branch = list/re-propose, never retry the same id.
        raise ToolError(str(exc), code="unknown_proposal") from exc
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
        # str(KeyError) is the repr of its message — the error used to arrive
        # double-quoted on the wire (round-2 UX audit).
        raise ToolError(exc.args[0] if exc.args else str(exc),
                        code="not_found") from exc
    text = info.path.read_text(encoding="utf-8") if info.path else ""
    # round AA (goal item 8): content served over MCP is the same usage
    # signal core/evaluate.py reports on — best-effort, never blocks the show.
    try:
        append_event(project.root, "ai", "skill_used", {"skill": skill_id, "via": "mcp"})
    except Exception:
        pass
    return {"skill": info.to_dict(), "text": text}


def _h_agent_surface(project: Project, args: dict) -> dict:
    """Read-only: the AgentSurfaceManifestV1 for the CURRENT profile (§ DR05
    ruling 6). Derived + rebuildable, never persisted; honest about scope
    (``raw_filesystem_enforced: false``, ``project_can_override: false``).

    The manifest is a pure function of (registry, profile). :func:`call_tool`
    routes this tool with the LIVE profile it was called under; this default
    build (collaborative) keeps the handler valid if invoked directly."""
    return _P.resolve_agent_surface(TOOL_DEFS, _P.COLLABORATIVE).manifest()


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


Handler = Callable[[Project, dict], dict]

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "status",
        "description": "Project status snapshot: project/preset/mode, per-state shot "
        "counts, timeline, latest final, QC summary, spend, and a "
        "suggested next step.",
        "inputSchema": _EMPTY_SCHEMA,
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_status,
    },
    {
        "name": "explain",
        "description": "Why will the next build do what it will do? Read-only, never "
        "spends: per-shot picture/voice states with hash evidence, "
        "timeline fingerprint diff, final/proxy content-key verdicts. "
        "graph=true appends the derived explicit-DAG diagnostics view.",
        "inputSchema": _schema(
            {
                "graph": {
                    "type": "boolean",
                    "description": "append the read-only explicit-DAG "
                    "dependency view (manju.graph-diagnostics/v1: "
                    "phase/shot/render/export)",
                },
            }
        ),
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_explain,
    },
    {
        "name": "impact",
        "description": "If this shot (or a hypothetical field edit) changes, what "
        "happens? Read-only, never spends: video/voice would-become, "
        "caption cues, timeline recompile, final re-render, export "
        "deliverables, catch-up cost.",
        "inputSchema": _schema(
            {
                "shot_id": {"type": "string", "description": "e.g. S002"},
                "field": {
                    "type": "string",
                    "description": "optional dotted path, e.g. dialogue.text",
                },
                "value": {
                    "type": "string",
                    "description": "optional new value for field (hypothetical)",
                },
            },
            ["shot_id"],
        ),
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_impact,
    },
    {
        "name": "check",
        "description": "Run the safety net: schema + referential integrity + hard lock "
        "verification + secret scan. Returns {ok, errors, warnings}.",
        "inputSchema": _EMPTY_SCHEMA,
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_check,
    },
    {
        "name": "list_shots",
        "description": "List every shot with its build state (missing/fresh/stale/"
        "manual/needs_selection/broken), selected take, and a note.",
        "inputSchema": _EMPTY_SCHEMA,
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_list_shots,
    },
    {
        "name": "get_shot",
        "description": "Read one shot file: raw YAML text + the parsed mapping + `rev`, "
        "a content hash. Pass `rev` back as update_shot's `expected_rev` "
        "for optimistic-concurrency (CAS) protection against a stale "
        "overwrite.",
        "inputSchema": _schema(
            {"shot_id": {"type": "string", "description": "e.g. S002"}},
            ["shot_id"],
        ),
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_get_shot,
    },
    {
        "name": "update_shot",
        "description": "The ONLY tool that writes a shot file. Validates schema, refuses "
        "to add/remove/change locks or edit a locked field, writes "
        "atomically, then re-checks and rolls back if the edit breaks the "
        "shot.",
        "inputSchema": _schema(
            {
                "shot_id": {"type": "string"},
                "yaml_content": {
                    "type": "string",
                    "description": "Full replacement YAML for the shot file.",
                },
                "expected_rev": {
                    "type": "string",
                    "description": "Optional CAS token: the `rev` get_shot "
                    "returned when you loaded this shot. If the shot changed "
                    "since (another entrance wrote it), the write is refused "
                    "instead of silently overwriting — call get_shot again for "
                    "the current rev and retry. Omit to skip the check.",
                },
            },
            ["shot_id", "yaml_content"],
        ),
        "policy": _P.policy(
            effects=[_P.WRITE_TRUTH],
            gate=[_P.GATE_CAS, _P.GATE_CHECKED_WRITE, _P.GATE_BUILD_LOCK],
            concurrency=_P.CONC_BUILD_LOCK,
            unattended=_P.ALLOW_WITH_CAS,
            writes=["shots/"],
        ),
        "handler": _h_update_shot,
    },
    {
        "name": "select_take",
        "description": "Set a shot's selected_take (the take must already exist). The "
        "decision is one line of text truth.",
        "inputSchema": _schema(
            {"shot_id": {"type": "string"}, "take": {"type": "string"}},
            ["shot_id", "take"],
        ),
        "policy": _P.policy(
            effects=[_P.WRITE_TRUTH],
            gate=[_P.GATE_CHECKED_WRITE, _P.GATE_BUILD_LOCK],
            concurrency=_P.CONC_BUILD_LOCK,
            unattended=_P.ALLOW,
            writes=["shots/"],
        ),
        "handler": _h_select_take,
    },
    {
        "name": "build",
        "description": "One-command build: fill gaps → compile timeline → render → QC "
        "→ exports. Never overturns an existing selection. May call paid "
        "providers and spend real money (生成缺口会花钱); the spend gate/budget "
        "apply — dry_run:true to estimate first.",
        "inputSchema": _schema(
            {
                "target": {
                    "type": "string",
                    "enum": ["proxy", "final", "exports", "qc"],
                    "default": "final",
                    "description": "qc does NOT render — it QCs the newest "
                    "EXISTING renders/final/*.mp4 against a freshly recompiled "
                    "timeline (use target=final first for QC on a fresh "
                    "render); the result is named qc_final.",
                },
                "gen": {
                    "type": "string",
                    "enum": ["missing", "auto", "off"],
                    "default": "missing",
                },
                "regen_stale": {"type": "boolean", "default": False},
                "dry_run": {"type": "boolean", "default": False},
                "assume_yes": {
                    "type": "boolean",
                    "default": False,
                    "description": "Confirm spend gate after dry_run estimate (§8.3)",
                },
                "lang": {
                    "type": "string",
                    "description": "Optional locale id (WP4) — voice+captions+final "
                    "under locales/<lang>/; only target final|qc",
                },
            }
        ),
        "policy": _P.policy(
            effects=[_P.WRITE_TRUTH, _P.WRITE_DERIVED, _P.NETWORK, _P.SPEND],
            network=_P.POSSIBLE,
            spend=_P.POSSIBLE,
            gate=[_P.GATE_SPEND_GATE, _P.GATE_BUILD_LOCK],
            concurrency=_P.CONC_BUILD_LOCK,
            unattended=_P.DRY_RUN_ONLY,
            writes=["shots/", "renders/", "media/gen/", "timeline.json",
                    "captions/", "reports/"],
        ),
        "handler": _h_build,
    },
    {
        "name": "redo",
        "description": "Force new takes for one shot (append-only; an existing "
        "selection stands unless the shot had none). May call paid providers "
        "and spend real money (重做即生成,会花钱); the spend gate/budget apply. "
        "Pass assume_yes=true only after the human confirmed the estimate.",
        "inputSchema": _schema(
            {
                "shot_id": {"type": "string"},
                "candidates": {"type": "integer"},
                "provider": {"type": "string"},
                "seed": {"type": "integer"},
                "assume_yes": {
                    "type": "boolean",
                    "description": "Confirm spend gate (§8.3); default false",
                },
            },
            ["shot_id"],
        ),
        "policy": _P.policy(
            effects=[_P.WRITE_DERIVED, _P.NETWORK, _P.SPEND],
            network=_P.POSSIBLE,
            spend=_P.POSSIBLE,
            gate=[_P.GATE_SPEND_GATE, _P.GATE_BUILD_LOCK],
            concurrency=_P.CONC_BUILD_LOCK,
            unattended=_P.DENY,
            writes=["media/gen/", "shots/"],
        ),
        "handler": _h_redo,
    },
    {
        "name": "qc",
        "description": "Three-layer QC over the compiled timeline; writes qc.json, "
        "qc.md, repair_plan.yaml. Returns {ok, items, reports, "
        "assurance}; assurance is a derived per-shot bound-acceptance "
        "block (accepted/rejected/unknown/stale/…) + read-only repair "
        "proposals — a separate axis from `ok` (never changes it). "
        "Pass `lang` or `final_path` to probe a locale final under "
        "renders/final/locales/<lang>/ instead of the base final.",
        "inputSchema": _schema({
            "deep": {"type": "boolean", "default": False},
            "lang": {
                "type": "string",
                "description": "Optional locale id — QC newest final under "
                "renders/final/locales/<lang>/",
            },
            "final_path": {
                "type": "string",
                "description": "Optional project-relative or absolute final "
                "mp4 to probe (overrides lang)",
            },
        }),
        "policy": _P.policy(
            effects=[_P.WRITE_DERIVED],
            gate=[_P.GATE_BUILD_LOCK],
            concurrency=_P.CONC_BUILD_LOCK,
            unattended=_P.ALLOW,
            writes=["reports/"],
        ),
        "handler": _h_qc,
    },
    {
        "name": "qc_brief",
        "description": "Package a visual-QC review for a VISION-CAPABLE agent to judge "
        "(Manju runs no vision model). Per shot: review frames (mid + "
        "first/last of the selected take) + context (scene, characters "
        "with bible ref images, must_show/avoid, continuity locks, "
        "dialogue) + the visual-qc-review criteria pointer + the verdict "
        "JSON shape. `shots` scopes it; omit for all reviewable shots. "
        "Then read `skills show visual-qc-review` and return findings via "
        "qc_verdict. mode=consistency instead briefs CROSS-shot units: "
        "one contact sheet per character in >1 shot (identity/outfit "
        "drift), one side-by-side board per adjacent same-scene pair, one "
        "sheet per scene (lighting/continuity) — return those verdicts "
        "keyed by `unit` (binds all member take hashes).",
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
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_qc_brief,
    },
    {
        "name": "qc_coverage",
        "description": "Per-shot AND per-consistency-unit AI-judgment coverage: "
        "{shots:{id:state}, units:{id:{state,kind,label}}, "
        "summary:{…,gaps}}. state = reviewed (a verdict's bound bytes "
        "match the current take) / stale (a verdict exists but bytes "
        "moved) / never (no verdict recorded). summary.gaps is the "
        "never-reviewed count run_qc also surfaces.",
        "inputSchema": _EMPTY_SCHEMA,
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_qc_coverage,
    },
    {
        "name": "qc_verdict",
        "description": "Intake the agent's structured visual-QC verdicts and append to "
        "reports/qc_agent.jsonl, each BOUND to the take bytes it judged. "
        "`verdicts` is an array of {shot, take, criterion, "
        "level(blocker|issue|fyi), message(中文), evidence, frame_ms?}. The "
        "next qc pass surfaces matching verdicts as [AI判读] items "
        "(blocker→error / issue→warn / fyi→info); regenerating a take "
        "makes its old verdicts stale; an unknown shot is rejected. A "
        "verdict may instead carry `unit` (a comparison-unit id from "
        "qc_brief mode=consistency), binding all that unit's member take "
        "hashes. Pass `from_file` (a project-relative JSON path) instead "
        "of inline `verdicts`.",
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
        "policy": _P.policy(
            effects=[_P.WRITE_DERIVED],
            concurrency=_P.CONC_ATOMIC_APPEND,
            unattended=_P.ALLOW,
            writes=["reports/qc_agent.jsonl"],
        ),
        "handler": _h_qc_verdict,
    },
    {
        "name": "export",
        "description": "Export from the compiled timeline. Requires timeline.json. "
        "Returns {outputs: {format: relpath}}. When the project lists "
        "final_export in ask_before this refuses with code=waiting_user until "
        "a human confirms — re-call with assume_yes: true (JSON true only).",
        "inputSchema": _schema(
            {
                "formats": {
                    "type": "array",
                    # TRISURFACE F-19: CLI-parity (srt writes srt+ass+vtt; vtt
                    # is an alias of the same caption writer).
                    "items": {"type": "string",
                              "enum": ["srt", "vtt", "ttml", "otio", "edl",
                                       "fcpxml", "xmeml", "jianying", "capcut"]},
                    "minItems": 1,
                },
                "assume_yes": {"type": "boolean", "default": False},
            },
            ["formats"],
        ),
        "policy": _P.policy(
            effects=[_P.WRITE_DERIVED],
            gate=[_P.GATE_BUILD_LOCK],
            concurrency=_P.CONC_BUILD_LOCK,
            unattended=_P.ALLOW,
            writes=["captions/", "exports/"],
        ),
        "handler": _h_export,
    },
    {
        "name": "events",
        "description": "Tail the collaboration log (who did what, when — §10).",
        "inputSchema": _schema({"n": {"type": "integer", "default": 20}}),
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_events,
    },
    {
        "name": "board",
        "description": "Generate the static HTML review board and return its path.",
        "inputSchema": _EMPTY_SCHEMA,
        "policy": _P.policy(
            effects=[_P.WRITE_DERIVED],
            unattended=_P.ALLOW,
            writes=["board.html"],
        ),
        "handler": _h_board,
    },
    {
        "name": "propose",
        "description": "Write a proposal to proposals/ — the agent's legitimate channel "
        "to request a locked-content change. Returns {path}.",
        "inputSchema": _schema(
            {"title": {"type": "string"}, "body": {"type": "string"}},
            ["title", "body"],
        ),
        "policy": _P.policy(
            effects=[_P.WRITE_PROPOSAL],
            gate=[_P.GATE_PROPOSAL_APPEND],
            concurrency=_P.CONC_ATOMIC_APPEND,
            unattended=_P.ALLOW,
            writes=["proposals/"],
        ),
        "handler": _h_propose,
    },
    {
        "name": "director_propose",
        "description": "PROPOSE a plan for the AI-director loop (step 1). `actions` is a "
        "non-empty array of whitelisted action objects, each with a "
        "`type` "
        "(build|redo|voice|repair|mixer|captions|packaging|snapshot|rollback) "
        "1:1 with an engine entry point; each is shape-validated and "
        "annotated with its impact (affected shots/outputs) and est cost "
        "(the same dry-run estimators). Persists "
        "reports/proposals/<id>.yaml and returns the full proposal. "
        "Nothing runs, nothing is spent — relay the cost to the human, "
        "then confirm. Example: {\"type\":\"redo\",\"shot\":\"S002\"}.",
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
        "policy": _P.policy(
            effects=[_P.WRITE_PROPOSAL],
            gate=[_P.GATE_PROPOSAL_APPEND],
            concurrency=_P.CONC_ATOMIC_APPEND,
            unattended=_P.ALLOW,
            writes=["reports/proposals/"],
        ),
        "handler": _h_director_propose,
    },
    {
        "name": "director_confirm",
        "description": "CONFIRM a proposal (step 3) — the explicit "
        "approve-before-execute gate. NEVER call without first relaying "
        "the proposal's impact + est cost to the human and getting their "
        "yes. A proposal whose project changed since is refused as "
        "待更新/expired. Returns the updated proposal.",
        "inputSchema": _schema(
            {"id": {"type": "string", "description": "proposal id, e.g. prop_0001"}},
            ["id"],
        ),
        "policy": _P.policy(
            effects=[_P.WRITE_PROPOSAL],
            gate=[_P.GATE_PROPOSAL_APPEND],
            concurrency=_P.CONC_ATOMIC_APPEND,
            unattended=_P.DENY,
            writes=["reports/proposals/"],
        ),
        "handler": _h_director_confirm,
    },
    {
        "name": "director_execute",
        "description": "EXECUTE a CONFIRMED proposal (steps 4-6): runs its actions in "
        "order through the real engine, auto-snapshots BEFORE mutating "
        "(rollback is one step), stops at the first failure, returns the "
        "structured diff (truth text + finals) + next-step suggestions. "
        "Paid steps ride the proposal's assume_yes; the spend gate still "
        "applies engine-side. Only a confirmed, current proposal "
        "executes.",
        "inputSchema": _schema(
            {"id": {"type": "string", "description": "proposal id, e.g. prop_0001"}},
            ["id"],
        ),
        "policy": _P.policy(
            effects=[_P.WRITE_TRUTH, _P.WRITE_DERIVED, _P.NETWORK, _P.SPEND],
            network=_P.POSSIBLE,
            spend=_P.POSSIBLE,
            gate=[_P.GATE_CONFIRMED_PROPOSAL, _P.GATE_SPEND_GATE, _P.GATE_BUILD_LOCK],
            concurrency=_P.CONC_BUILD_LOCK,
            unattended=_P.CONFIRMED_PROPOSAL_ONLY,
            writes=["shots/", "renders/", "media/gen/", "reports/proposals/"],
        ),
        "handler": _h_director_execute,
    },
    {
        "name": "director_suggest",
        "description": "SUGGEST NEXT: deterministic next-step nudges from existing "
        "signals (funnel-first→write, missing→generate, stale→redo, "
        "needs_selection→select, QC→repair, budget→remind). Most carry a "
        "ready-made action payload you can pass straight to "
        "director_propose; some (funnel/select/budget) are advisory-only "
        "with `action: null`. Read-only.",
        "inputSchema": _EMPTY_SCHEMA,
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_director_suggest,
    },
    {
        "name": "funnel_status",
        "description": "创作漏斗状态 / creation-funnel status: the staged workflow as data — "
        "立意→梗概→节拍→剧本→分镜→生成计划→生成. Per-stage {id, cn(中文名), "
        "state(done|current|todo), artifact, evidence, skill, "
        "next_action}; the first not-done stage is `current`. Read-only — "
        "detects progress from files on disk, scaffolds nothing, spends "
        "nothing. Use it to know what to write next before generating.",
        "inputSchema": _EMPTY_SCHEMA,
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_funnel_status,
    },
    {
        "name": "skill_list",
        "description": "技能库索引 / skill index: every visible skill (project > user > "
        "bundled) with id, 何时用 one-liner, tags. Progressive disclosure — "
        "read this cheap index, then pull ONE skill's full text via "
        "skill_show; never inline the whole library.",
        "inputSchema": _EMPTY_SCHEMA,
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_skill_list,
    },
    {
        "name": "skill_show",
        "description": "One skill's FULL SKILL.md text by id (project > user > "
        "bundled resolution). Load on demand when the index says it applies to "
        "the task at hand.",
        "inputSchema": _schema({"id": {"type": "string"}}, ["id"]),
        # a pure read; the best-effort skill_used telemetry append to events.jsonl
        # is not a policy-relevant write (never blocks, never mutates truth).
        "policy": _P.policy(effects=[_P.READ]),
        "handler": _h_skill_show,
    },
    {
        "name": "agent_surface",
        "description": "Read-only: the agent-surface manifest for the CURRENT profile "
        "(manju.agent-surface/v1) — per-tool "
        "effects/network/spend/gate/concurrency/unattended projection, "
        "which tools are listed, and a stable digest. Honest about scope: "
        "raw_filesystem_enforced=false, project_can_override=false (MCP "
        "policy governs only this server's tool calls — it does not "
        "sandbox the filesystem, and project content cannot change it).",
        "inputSchema": _EMPTY_SCHEMA,
        "policy": _P.policy(effects=[_P.READ_RUNTIME]),
        "handler": _h_agent_surface,
    },
]

TOOLS: dict[str, dict[str, Any]] = {t["name"]: t for t in TOOL_DEFS}

# AI_IDE_16 §10: handlers that accept the LIVE agent profile (for the keyframe
# spend gate). The profile is the server flag, never the agent's arguments.
_PROFILE_AWARE_TOOLS = frozenset({"build", "director_execute"})

# Load-time integrity gate (§7.8): every tool declares a legal policy; the enums
# and cross-field rules hold. A malformed policy fails the import loudly rather
# than silently shipping a broken agent surface.
_P.validate_registry(TOOL_DEFS)


def list_tools(profile: str = _P.COLLABORATIVE) -> list[dict[str, Any]]:
    """The ``tools/list`` payload for ``profile``: name + description +
    inputSchema only (no policy leaks onto the wire). The listed set comes from
    the ONE resolver — collaborative lists every tool; unattended hides the
    DENY tools. Order follows the registry."""
    listed = set(_P.resolve_agent_surface(TOOL_DEFS, profile).listed_names())
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in TOOL_DEFS
        if t["name"] in listed
    ]


def call_tool(
    project: Project,
    name: str,
    arguments: dict | None,
    profile: str = _P.COLLABORATIVE,
) -> dict:
    """Dispatch a ``tools/call`` under ``profile``. Raises :class:`ToolError` for
    an unknown tool and :class:`AgentProfileDenied` (structured) for a tool the
    profile refuses — both are turned into ``isError`` results by the server.

    Enforcement is HERE, at call dispatch (the single resolver), plus the engine
    guards each handler already runs (locks, CAS, build lock, spend gate,
    director confirm). The policy metadata is never parsed as a permission and is
    never the security boundary."""
    # Round-2 UX audit: a non-string name used to surface as "unhashable
    # type: 'dict'" from the registry lookup — name the actual problem.
    if not isinstance(name, str):
        raise ToolError(
            f"tool name must be a string, got {type(name).__name__}",
            code="invalid_argument")
    entry = TOOLS.get(name)
    if entry is None:
        # TRISURFACE F-18: its own code — an agent that mistyped a tool name
        # should re-read tools/list, not retry the same call.
        raise ToolError(f"unknown tool: {name!r}", code="unknown_tool")
    args = arguments or {}
    # Round-2 UX audit: the advertised `required` arrays were never enforced —
    # a missing arg surfaced as the bare KeyError text ({"error": "'shot_id'"}).
    # Enforce the tool's OWN advertised schema at the one dispatch point.
    missing = [k for k in entry["inputSchema"].get("required", []) if k not in args]
    if missing:
        raise ToolError(
            f"missing required argument(s): {', '.join(missing)} "
            f"(see tools/list inputSchema for {name})",
            code="invalid_argument")
    surface = _P.resolve_agent_surface(TOOL_DEFS, profile, call_arguments=args)
    decision = surface.decide(name, args)
    if not decision.admitted:
        raise AgentProfileDenied(decision.denial_payload())
    if name == "agent_surface":
        # a pure function of (registry, profile) — return THIS profile's manifest
        return surface.manifest()
    # AI_IDE_16 §10: the two paid-video handlers honor the keyframe spend gate,
    # so they need the LIVE profile (server-flag trusted — NEVER read from the
    # agent's `args`). Every other handler keeps the (project, args) signature.
    if name in _PROFILE_AWARE_TOOLS:
        return entry["handler"](project, args, profile=profile)
    return entry["handler"](project, args)

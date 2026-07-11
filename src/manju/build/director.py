"""The AI-director loop — the six-step collaboration contract as one object.

Goal item 17 (§9 of REPORTS/ROUND-U-REFERENCES.md). Manju's stance (§0): the
LLM / natural-language understanding lives in the EXTERNAL agent (Claude / Codex
/ the human at the GUI). Manju itself never calls an LLM. What Manju ships is the
DETERMINISTIC six-step contract any agent (or the GUI user) drives:

    read state → propose a plan → explain impact & cost risk → wait for an
    explicit confirmation → execute → show the diff → suggest the next step.

Every competitor (item 9 of the references) implements some of these steps;
none implements all six as one auditable object. This module makes the loop a
first-class, persistent, human-editable thing instead of scattered pieces:

- A **Proposal** is truth-as-text (§3): ``reports/proposals/<id>.yaml``, a
  validated-but-hand-editable YAML file, NOT a disposable ``.manju`` runtime
  blob. Proposals are collaboration truth — they belong next to the events log.
- Its **lifecycle** is a small state machine::

      proposed → confirmed → executing → done | failed | rejected
                                       ↘ expired (待更新: the project moved)

- **propose()** takes actions from a FIXED whitelisted vocabulary, each mapping
  1:1 to an existing engine entry point (build / redo / voice / repair / mixer /
  captions / packaging / snapshot / rollback). It validates each action's shape
  strictly, annotates it with its IMPACT (affected shots/outputs, via the same
  staleness machinery) and its EST COST (the SAME estimators the GUI plan modal
  and ``build --dry-run`` use — never a parallel estimator), and stamps a STATE
  FINGERPRINT so the proposal EXPIRES honestly if the project changed underneath.
- **confirm()** is an explicit, separate call — never implied by propose or
  execute. This is the approve-before-spend boundary the references (item 9)
  call the move no creative tool makes.
- **execute()** runs the actions strictly in order through the real engine
  functions, auto-snapshots BEFORE mutating (so 还原 is one ``rollback``),
  stops at the first failure with a :mod:`manju.core.failures` record, and
  produces a structured DIFF (spec changes via ``gitops`` diff + output changes
  via the finals' content keys / ``compare``). Paid actions stay behind the
  EXISTING ask_before / spend-gate at execute time too (defense in depth): the
  ``assume_yes`` that unlocks them comes ONLY from the proposal's confirmed state.
- **suggest_next()** turns the existing signals (staleness, QC, missing
  deliverables, budget) into ready-made action payloads an agent/user can pass
  straight back into ``propose()`` — closing the loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from ..core.container import Project, ProjectError
from ..core.events import append_event
from ..core.hashing import hash_value
from ..core.yamlio import read_yaml, write_yaml
from .stale import ShotState, evaluate_all

__all__ = [
    "DirectorError",
    "ActionError",
    "Proposal",
    "ProposalAction",
    "Outcome",
    "Suggestion",
    "ACTION_TYPES",
    "propose",
    "confirm",
    "reject",
    "execute",
    "suggest_next",
    "load_proposal",
    "list_proposals",
    "state_fingerprint",
    "proposals_dir",
]


class DirectorError(RuntimeError):
    """Clean one-line failures for the CLI/MCP/GUI (FIX-D envelope)."""


class ActionError(DirectorError):
    """One action failed during execute — carries the structured failure record
    so the first-failure stop can log it and surface it."""

    def __init__(self, message: str, *, subject: str = "", evidence: str = "",
                 hint: str = ""):
        super().__init__(message)
        self.subject = subject
        self.evidence = evidence
        self.hint = hint


# The FIXED whitelisted action vocabulary — each maps 1:1 to an existing engine
# entry point. Nothing outside this set can ever be proposed or executed.
ACTION_TYPES = (
    "build",      # build/graph.run_build
    "redo",       # build/graph.redo_shot
    "voice",      # build/graph.voice_batch (single shot)
    "repair",     # media/repair_ops.{retime,extend,trim,set_inout,crop_pad}_take
    "mixer",      # build/mixer.apply_mixer
    "captions",   # gui/captions_edit (+ rules.captions.mode flip)
    "packaging",  # packaging.yaml edit / media/packaging.make_package
    "snapshot",   # core/history.snapshot
    "rollback",   # core/history.{rollback_shot,rollback_file}
)

# The step that spends money — priced through the shared estimators. Everything
# else is a local/text operation and prices as 0 (honest: a repair is local
# ffmpeg, a mixer edit is one line of YAML, a snapshot is a git commit).
_PRICED_TYPES = ("build", "redo", "voice")


def _has_priced_action(proposal) -> bool:
    """True if the proposal contains any action that can spend real money
    (build/redo/voice). Round Y (#15): the human-only confirmation gate keys on
    this — free proposals stay AI-confirmable, paid ones need a human."""
    return any(pa.action.get("type") in _PRICED_TYPES for pa in proposal.actions)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _actor(actor: str | None) -> str:
    return actor or "human"


# ------------------------------------------------------------- persisted shape


class ProposalAction(BaseModel):
    """One whitelisted action plus its pre-computed impact + est cost, and (after
    execute) its result. The ``action`` dict is the validated payload."""

    action: dict[str, Any]
    impact: dict[str, Any] = Field(default_factory=dict)
    estimated_cost: float = 0.0
    currency: str | None = None
    result: dict[str, Any] | None = None


class Proposal(BaseModel):
    """A collaboration proposal — the six-step contract made persistent (§3).

    Written to ``reports/proposals/<id>.yaml`` as human-editable truth. The model
    is lenient enough to round-trip a hand-edited file, strict enough that a
    bogus one is caught on load."""

    id: str
    state: str = "proposed"  # proposed|confirmed|executing|done|failed|rejected|expired
    why: str = ""
    actor: str = "human"
    created_at: str = ""
    fingerprint: str = ""  # project state fingerprint at propose time
    actions: list[ProposalAction] = Field(default_factory=list)
    estimated_cost: float = 0.0
    currency: str | None = None
    confirmed_at: str | None = None
    confirmed_by: str | None = None
    executed_at: str | None = None
    outcome: dict[str, Any] | None = None

    def summary(self, *, current: bool | None = None) -> dict[str, Any]:
        """The compact list-row shape (no per-action detail)."""
        return {
            "id": self.id,
            "state": self.state,
            "why": self.why,
            "actor": self.actor,
            "created_at": self.created_at,
            "actions": len(self.actions),
            "estimated_cost": self.estimated_cost,
            "currency": self.currency,
            "current": current,
        }


@dataclass
class Outcome:
    """The result of :func:`execute` — the step-5 "show the diff" + step-6
    "suggest next" payload the CLI/MCP/GUI render."""

    proposal_id: str
    ok: bool
    state: str
    snapshot: dict[str, Any] | None = None  # {sha, label} pre-mutation, or None
    results: list[dict[str, Any]] = field(default_factory=list)
    failure: dict[str, Any] | None = None
    diff: dict[str, Any] = field(default_factory=dict)
    suggestions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "ok": self.ok,
            "state": self.state,
            "snapshot": self.snapshot,
            "results": self.results,
            "failure": self.failure,
            "diff": self.diff,
            "suggestions": self.suggestions,
        }


@dataclass
class Suggestion:
    """A deterministic next-step nudge carrying a ready-made action payload the
    agent/user can pass straight into :func:`propose`."""

    kind: str           # funnel | redo | repair | generate | package | budget | select
    text: str           # 中文, user-facing
    action: dict[str, Any] | None = None  # None = advisory only (no proposal action)
    shot: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "action": self.action,
                "shot": self.shot}


# --------------------------------------------------------------- storage paths


def proposals_dir(project: Project):
    """``reports/proposals/`` — collaboration truth lives next to the reports."""
    return project.reports_dir / "proposals"


_ID_RE = re.compile(r"^prop_\d{4,}$")


def _proposal_path(project: Project, proposal_id: str):
    if not _ID_RE.fullmatch(proposal_id):
        raise DirectorError(f"invalid proposal id: {proposal_id!r}")
    return proposals_dir(project) / f"{proposal_id}.yaml"


def _next_id(project: Project) -> str:
    highest = 0
    d = proposals_dir(project)
    if d.exists():
        for f in d.glob("prop_*.yaml"):
            m = re.match(r"prop_(\d+)", f.name)
            if m:
                highest = max(highest, int(m.group(1)))
    return f"prop_{highest + 1:04d}"


def _save(project: Project, proposal: Proposal) -> None:
    d = proposals_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    write_yaml(d / f"{proposal.id}.yaml", proposal.model_dump())


def load_proposal(project: Project, proposal_id: str) -> Proposal:
    path = _proposal_path(project, proposal_id)
    if not path.exists():
        raise DirectorError(f"no such proposal: {proposal_id}")
    data = read_yaml(path)
    if not isinstance(data, dict):
        raise DirectorError(f"proposal file is not a mapping: {path.name}")
    try:
        return Proposal.model_validate(data)
    except Exception as exc:  # a hand-broken proposal → one clean line, never a stack
        raise DirectorError(
            f"proposal {proposal_id} is invalid: {' '.join(str(exc).split())}") from exc


def list_proposals(project: Project) -> list[Proposal]:
    """Every proposal on disk, NEWEST FIRST (by id)."""
    d = proposals_dir(project)
    if not d.exists():
        return []
    out: list[Proposal] = []
    for f in sorted(d.glob("prop_*.yaml"), reverse=True):
        try:
            data = read_yaml(f)
            if isinstance(data, dict):
                out.append(Proposal.model_validate(data))
        except Exception:
            continue  # a torn/foreign file never bricks the list
    return out


# --------------------------------------------------------- state fingerprint


def _file_hash(path) -> str:
    try:
        return hash_value(path.read_text(encoding="utf-8")) if path.exists() else ""
    except OSError:
        return ""


def state_fingerprint(project: Project) -> str:
    """A hash of the project state a proposal was reasoned against — index order,
    per-shot spec hash + selected take + build state, plus the rules/packaging
    text. A change to ANY of these makes an open proposal 待更新 (expired), so a
    plan is never executed against a project that moved underneath it."""
    shots = [
        [st.shot_id, st.spec_hash, st.selected_take or "", st.state.value]
        for st in evaluate_all(project)
    ]
    return hash_value({
        "shots": shots,
        "rules": _file_hash(project.rules_path),
        "packaging": _file_hash(project.packaging_path),
        # Round Y (review #45): fold the project files that steer COST and
        # ROUTING so a confirmed plan expires when the money/provider picture
        # changes underneath it — project.yaml (budget, build mode, ask_before)
        # and timeline/routing.yaml (which provider fires, at what price). A
        # confirmed proposal was priced against these; a change must re-price.
        # (Provider MANIFESTS live in ~/.manju — machine-level config, not
        # project truth — so they stay out of the project fingerprint; the
        # ask_before/spend gate at execute time is the remaining backstop.)
        "config": _file_hash(project.root / "project.yaml"),
        "routing": _file_hash(project.root / "timeline" / "routing.yaml"),
    })


def _is_current(project: Project, proposal: Proposal) -> bool:
    return proposal.fingerprint == state_fingerprint(project)


# ----------------------------------------------------------- action validation


def _shot_exists(project: Project, shot_id: str) -> bool:
    return bool(shot_id) and shot_id in project.shot_ids()


def _req_str(action: dict, key: str) -> str:
    v = action.get(key)
    if not isinstance(v, str) or not v.strip():
        raise DirectorError(f"action.{key} is required (a non-empty string)")
    return v.strip()


def _validate_action(project: Project, action: Any) -> dict[str, Any]:
    """Strict shape validation for one whitelisted action → the normalized dict.
    Raises :class:`DirectorError` on any malformed action (propose fails cleanly,
    never spends)."""
    if not isinstance(action, dict):
        raise DirectorError("each action must be an object with a 'type'")
    atype = action.get("type")
    if atype not in ACTION_TYPES:
        raise DirectorError(
            f"unknown action type {atype!r}; the whitelist is {list(ACTION_TYPES)}")

    if atype == "build":
        from .graph import GEN_MODES  # round W (issue #3): single source of truth

        target = action.get("target", "final")
        gen = action.get("gen", "missing")
        if target not in ("proxy", "final", "exports", "qc"):
            raise DirectorError(f"build.target must be proxy|final|exports|qc (got {target!r})")
        if gen not in GEN_MODES:
            raise DirectorError(f"build.gen must be {'|'.join(GEN_MODES)} (got {gen!r})")
        return {"type": "build", "target": target, "gen": gen,
                "regen_stale": bool(action.get("regen_stale")),
                "force": bool(action.get("force"))}

    if atype == "redo":
        shot = _req_str(action, "shot")
        if not _shot_exists(project, shot):
            raise DirectorError(f"no such shot in this project: {shot}")
        out: dict[str, Any] = {"type": "redo", "shot": shot}
        for k in ("provider", "from_take"):
            if action.get(k) is not None:
                out[k] = str(action[k])
        for k in ("seed", "candidates"):
            if action.get(k) is not None:
                try:
                    out[k] = int(action[k])
                except (TypeError, ValueError):
                    raise DirectorError(f"redo.{k} must be an integer")
        return out

    if atype == "voice":
        shot = _req_str(action, "shot")
        if not _shot_exists(project, shot):
            raise DirectorError(f"no such shot in this project: {shot}")
        try:
            has_text = bool(project.load_shot(shot).dialogue.text)
        except ProjectError as exc:
            raise DirectorError(str(exc)) from exc
        if not has_text:
            raise DirectorError(f"{shot} has no dialogue.text to voice")
        out = {"type": "voice", "shot": shot}
        if action.get("provider") is not None:
            out["provider"] = str(action["provider"])
        return out

    if atype == "repair":
        op = action.get("op")
        if op not in ("retime", "extend", "trim", "inout", "croppad"):
            raise DirectorError(
                f"repair.op must be retime|extend|trim|inout|croppad (got {op!r})")
        shot = _req_str(action, "shot")
        if not _shot_exists(project, shot):
            raise DirectorError(f"no such shot in this project: {shot}")
        out = {"type": "repair", "op": op, "shot": shot}
        if action.get("take") is not None:
            out["take"] = str(action["take"])
        if op == "retime":
            out["factor"] = float(action.get("factor", 1.0))
            if out["factor"] <= 0:
                raise DirectorError("repair retime.factor must be > 0")
        elif op in ("extend", "trim"):
            try:
                out["ms"] = int(action.get("ms"))
            except (TypeError, ValueError):
                raise DirectorError(f"repair {op}.ms must be an integer (ms)")
            if out["ms"] <= 0:
                raise DirectorError(f"repair {op}.ms must be > 0")
            if op == "extend":
                out["mode"] = str(action.get("mode") or "freeze")
                if out["mode"] not in ("freeze", "pad_black"):
                    raise DirectorError("repair extend.mode must be freeze|pad_black")
        elif op == "inout":
            try:
                out["in_ms"], out["out_ms"] = int(action["in_ms"]), int(action["out_ms"])
            except (KeyError, TypeError, ValueError):
                raise DirectorError("repair inout needs integer in_ms and out_ms")
            if not (out["in_ms"] >= 0 and out["out_ms"] > out["in_ms"]):
                raise DirectorError("repair inout requires 0 <= in_ms < out_ms")
            out["mode"] = str(action.get("mode") or "virtual")
            if out["mode"] not in ("virtual", "reencode"):
                raise DirectorError("repair inout.mode must be virtual|reencode")
        else:  # croppad
            out["mode"] = str(action.get("mode") or "center_crop")
            if out["mode"] not in ("center_crop", "pad_blur"):
                raise DirectorError("repair croppad.mode must be center_crop|pad_blur")
        return out

    if atype == "mixer":
        changes = action.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise DirectorError("mixer.changes must be a non-empty object "
                                "(a subset of read_mixer's shape)")
        return {"type": "mixer", "changes": changes}

    if atype == "captions":
        op = action.get("op")
        if op not in ("save", "revert"):
            raise DirectorError(f"captions.op must be save|revert (got {op!r})")
        out = {"type": "captions", "op": op}
        if op == "save":
            from ..gui.captions_edit import CaptionEditError, normalize_cues, validate_cues
            try:
                cues = normalize_cues(action.get("cues"))
            except CaptionEditError as exc:
                raise DirectorError(f"captions.cues invalid: {exc}") from exc
            errors, _warn = validate_cues(cues)
            if errors:
                raise DirectorError("captions invalid: " + "; ".join(errors))
            out["cues"] = cues
        return out

    if atype == "packaging":
        op = action.get("op")
        if op not in ("apply", "package"):
            raise DirectorError(f"packaging.op must be apply|package (got {op!r})")
        out = {"type": "packaging", "op": op}
        if op == "apply":
            patch = action.get("patch")
            if not isinstance(patch, dict) or not patch:
                raise DirectorError("packaging apply needs a non-empty patch object")
            out["patch"] = patch
        else:
            out["force"] = bool(action.get("force"))
        return out

    if atype == "snapshot":
        return {"type": "snapshot", "label": str(action.get("label") or "")}

    # rollback
    op = action.get("op")
    if op not in ("shot", "file"):
        raise DirectorError(f"rollback.op must be shot|file (got {op!r})")
    if op == "shot":
        shot = _req_str(action, "shot")
        if not _shot_exists(project, shot):
            raise DirectorError(f"no such shot in this project: {shot}")
        return {"type": "rollback", "op": "shot", "shot": shot}
    path = _req_str(action, "path")
    return {"type": "rollback", "op": "file", "path": path,
            "ref": str(action.get("ref") or "HEAD")}


# ----------------------------------------------------------- impact + cost


def _plan_cost(project: Project, action: dict) -> tuple[dict[str, Any], float, str | None]:
    """Impact + est cost through the SAME estimators the GUI plan modal uses
    (gui/plan.action_plan), never a parallel one. Only build/redo/voice are
    priced; the rest are local/text ops (cost 0) with descriptive impact."""
    atype = action["type"]
    if atype in _PRICED_TYPES:
        from ..gui.plan import action_plan

        params = {k: v for k, v in action.items() if k != "type"}
        env = action_plan(project, atype, params)
        shots = [r.get("shot") for r in env.get("rows", []) if r.get("shot")]
        impact = {
            "shots": shots,
            "outputs": _priced_outputs(atype),
            "rows": env.get("rows", []),
            "skipped": env.get("skipped", []),
            "saved_cost": env.get("saved_cost", 0.0),
        }
        return impact, float(env.get("estimated_cost") or 0.0), env.get("currency")

    return _local_impact(project, action), 0.0, None


def _priced_outputs(atype: str) -> list[str]:
    if atype == "build":
        return ["timeline", "final"]
    if atype == "redo":
        return ["take (new)", "final"]
    return ["voice take", "timeline", "final"]  # voice


def _local_impact(project: Project, action: dict) -> dict[str, Any]:
    """Impact for the free (local/text) ops — affected shots + outputs, honest
    about the re-render fallout, computed WITHOUT mutating anything."""
    atype = action["type"]
    if atype == "repair":
        return {"shots": [action["shot"]], "outputs": ["take (new)", "final"],
                "note": f"{action['op']} → 追加新版本(append-only),选用后重渲染 final"}
    if atype == "mixer":
        shots = [s.get("shot") for s in (action["changes"].get("shots") or [])
                 if isinstance(s, dict) and s.get("shot")]
        return {"shots": shots, "outputs": ["final"],
                "segments_restale": shots,
                "note": "混音改动重渲染 final;逐镜素材原声改动才重算对应片段"}
    if atype == "captions":
        return {"shots": [], "outputs": ["captions", "final"],
                "note": ("接管为 manual 字幕并重烧 = final 重渲染"
                         if action["op"] == "save" else "还原自动字幕后重新生成")}
    if atype == "packaging":
        if action["op"] == "package":
            return {"shots": [], "outputs": ["cover", "teaser"],
                    "note": "从当前 final 切封面/预告"}
        return {"shots": [], "outputs": ["packaging", "final"],
                "note": "包装改动:片头/片尾/信息卡在 build 时生效"}
    if atype == "snapshot":
        return {"shots": [], "outputs": [], "note": "git 存档点(不改产物)"}
    if atype == "rollback":
        if action["op"] == "shot":
            return {"shots": [action["shot"]], "outputs": ["final"],
                    "note": "回到上一次选用(append-only,新版本仍在)"}
        return {"shots": [], "outputs": [action["path"]],
                "note": f"从 git 还原真相文本 {action['path']}"}
    return {"shots": [], "outputs": []}


# ------------------------------------------------------------------- propose


def propose(project: Project, actions: list[Any], *, why: str = "",
            actor: str | None = None) -> Proposal:
    """PROPOSE (step 1) — build a persistent, annotated plan object.

    ``actions`` is a list of whitelisted action dicts (see :data:`ACTION_TYPES`).
    Each is shape-validated strictly, annotated with its IMPACT (affected shots /
    outputs) and EST COST (the shared estimators — never a parallel one), and the
    whole proposal is stamped with a project STATE FINGERPRINT so it can expire
    honestly. Nothing is executed and nothing is spent; a malformed action raises
    :class:`DirectorError` before any file is written."""
    if not isinstance(actions, list) or not actions:
        raise DirectorError("propose needs a non-empty list of actions")
    actor = _actor(actor)

    entries: list[ProposalAction] = []
    total = 0.0
    currency: str | None = None
    for raw in actions:
        norm = _validate_action(project, raw)
        impact, cost, cur = _plan_cost(project, norm)
        entries.append(ProposalAction(action=norm, impact=impact,
                                      estimated_cost=cost, currency=cur))
        total += cost
        if cur and currency is None:
            currency = cur

    proposal = Proposal(
        id=_next_id(project),
        state="proposed",
        why=why,
        actor=actor,
        created_at=_now(),
        fingerprint=state_fingerprint(project),
        actions=entries,
        estimated_cost=round(total, 6),
        currency=currency,
    )
    _save(project, proposal)
    append_event(project.root, actor, "director_propose",
                 {"id": proposal.id, "actions": len(entries),
                  "estimated_cost": proposal.estimated_cost, "why": why})
    return proposal


# ------------------------------------------------------------------- confirm


def confirm(project: Project, proposal_id: str, actor: str | None = None) -> Proposal:
    """CONFIRM (step 3) — the explicit, SEPARATE approve-before-execute call.

    Never implied by propose or execute. A proposal that is no longer current
    (the project moved) is marked ``expired`` and refused — you confirm a plan
    against the state it was reasoned for, never a stale one."""
    actor = _actor(actor)
    proposal = load_proposal(project, proposal_id)
    if proposal.state != "proposed":
        raise DirectorError(
            f"proposal {proposal_id} is {proposal.state} — only a proposed plan can "
            "be confirmed (re-propose if it was rejected/expired/already run)")
    # Round Y (review #15): a HARD code-level human-only gate on paid proposals.
    # The tool description said "never confirm without a human yes", but that is
    # a prompt-level hope, not enforcement — an MCP/auto agent (actor="ai") could
    # propose→confirm→execute and self-approve real spend. Now a proposal that
    # contains ANY priced action (build/redo/voice) can only be confirmed by a
    # HUMAN actor: the CLI `manju director confirm` and the GUI 确认 button both
    # run as actor="human"; the MCP `director_confirm` tool runs as actor="ai"
    # and is refused here. Free (local/text) proposals stay AI-confirmable.
    if _has_priced_action(proposal) and actor != "human":
        raise DirectorError(
            f"proposal {proposal_id} 含付费动作(build/redo/voice)——付费提案必须由人类确认,"
            "不能由 AI 自行确认。请人在 `manju director confirm` 或 GUI 导演页点确认"
            "(AI 只能确认纯本地/文本类提案)")
    if not _is_current(project, proposal):
        proposal.state = "expired"
        _save(project, proposal)
        append_event(project.root, actor, "director_expire", {"id": proposal_id})
        raise DirectorError(
            f"proposal {proposal_id} 待更新 (expired): the project changed since it "
            "was proposed — re-propose against the current state")
    proposal.state = "confirmed"
    proposal.confirmed_at = _now()
    proposal.confirmed_by = actor
    _save(project, proposal)
    append_event(project.root, actor, "director_confirm", {"id": proposal_id})
    return proposal


def reject(project: Project, proposal_id: str, actor: str | None = None) -> Proposal:
    """Reject a proposal — a terminal decline. Kept on disk (history only grows)."""
    actor = _actor(actor)
    proposal = load_proposal(project, proposal_id)
    if proposal.state in ("done", "executing"):
        raise DirectorError(f"proposal {proposal_id} is {proposal.state} — cannot reject")
    proposal.state = "rejected"
    _save(project, proposal)
    append_event(project.root, actor, "director_reject", {"id": proposal_id})
    return proposal


# ------------------------------------------------------------------- execute


def execute(project: Project, proposal_id: str, actor: str | None = None,
            on_phase=None, *, agent_profile: str = "collaborative") -> Outcome:
    """EXECUTE (step 4) + SHOW DIFF (step 5) + SUGGEST NEXT (step 6).

    Runs the confirmed proposal's actions strictly IN ORDER through the real
    engine functions. Before mutating anything it auto-snapshots (so 还原 is one
    ``rollback``). It stops at the FIRST failure with a structured
    :mod:`manju.core.failures` record and marks the proposal ``failed``. On full
    success it marks it ``done``. Either way it produces a structured DIFF (truth
    text via ``gitops`` diff + finals via their content keys) and a set of
    next-step SUGGESTIONS.

    Defense in depth (§8.3): the ``assume_yes`` that lets build/redo/voice spend
    comes ONLY from the proposal's confirmed state — a proposal must be
    ``confirmed`` (and current) to execute, and only then is the gate released."""
    actor = _actor(actor)
    proposal = load_proposal(project, proposal_id)
    if proposal.state != "confirmed":
        raise DirectorError(
            f"proposal {proposal_id} is {proposal.state} — only a confirmed "
            "proposal can execute (confirm it first — confirm is a separate step)")
    # Round Y (review #15) belt-and-suspenders: even a proposal already marked
    # confirmed must have been confirmed BY A HUMAN if it spends — catches a
    # hand-edited/tampered proposal file that set confirmed_by to an ai actor.
    if _has_priced_action(proposal) and proposal.confirmed_by != "human":
        raise DirectorError(
            f"proposal {proposal_id} 含付费动作,但确认人不是 human"
            f"(confirmed_by={proposal.confirmed_by!r})——付费提案只能由人类确认后执行")
    if not _is_current(project, proposal):
        proposal.state = "expired"
        _save(project, proposal)
        append_event(project.root, actor, "director_expire", {"id": proposal_id})
        raise DirectorError(
            f"proposal {proposal_id} 待更新 (expired): the project changed since it "
            "was confirmed — re-propose against the current state")

    proposal.state = "executing"
    proposal.executed_at = _now()
    _save(project, proposal)
    append_event(project.root, actor, "director_execute_start",
                 {"id": proposal_id, "actions": len(proposal.actions)})

    # ---- auto-snapshot BEFORE mutating (还原 is one step). Best-effort: a
    # non-git project degrades to no snapshot (recorded honestly), never blocks.
    snapshot_info = _auto_snapshot(project, proposal_id)
    before_finals = _final_keys(project)

    results: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    for i, pa in enumerate(proposal.actions):
        _phase(on_phase, f"action {i + 1}/{len(proposal.actions)}: {pa.action['type']}")
        try:
            result = _run_action(project, pa.action, actor, on_phase,
                                 agent_profile=agent_profile)
        except ActionError as exc:
            failure = _record_action_failure(project, pa.action, exc, actor)
            pa.result = {"ok": False, "error": " ".join(str(exc).split())}
            results.append({"index": i, "type": pa.action["type"], "ok": False,
                            "error": pa.result["error"], "failure_id": failure.get("id")})
            break
        pa.result = {"ok": True, **result}
        results.append({"index": i, "type": pa.action["type"], "ok": True, **result})

    ok = failure is None
    proposal.state = "done" if ok else "failed"

    diff = _build_diff(project, snapshot_info, before_finals)
    suggestions = [s.to_dict() for s in suggest_next(project)]
    proposal.outcome = {
        "ok": ok, "snapshot": snapshot_info, "results": results,
        "failure": failure, "diff": diff,
    }
    _save(project, proposal)
    append_event(project.root, actor, "director_execute_done",
                 {"id": proposal_id, "ok": ok,
                  "failed_action": (failure or {}).get("subject")})
    return Outcome(proposal_id=proposal_id, ok=ok, state=proposal.state,
                   snapshot=snapshot_info, results=results, failure=failure,
                   diff=diff, suggestions=suggestions)


def _phase(on_phase, name: str) -> None:
    if on_phase is not None:
        try:
            on_phase(name)
        except Exception:
            pass


def _auto_snapshot(project: Project, proposal_id: str) -> dict[str, Any] | None:
    from ..core.history import HistoryError, snapshot

    try:
        info = snapshot(project, f"director {proposal_id} 执行前")
        return {"sha": info.get("sha"), "label": info.get("label"),
                "clean": info.get("clean")}
    except HistoryError as exc:
        # a non-git project can't snapshot — record why, but never block execute.
        return {"sha": None, "note": " ".join(str(exc).split())}


def _final_keys(project: Project) -> dict[str, str | None]:
    import json

    out: dict[str, str | None] = {}
    if not project.final_dir.exists():
        return out
    for p in sorted(project.final_dir.glob("final_v*.mp4")):
        sidecar = p.with_suffix(".key.json")
        key: str | None = None
        if sidecar.exists():
            try:
                key = json.loads(sidecar.read_text(encoding="utf-8")).get("final_key")
            except (json.JSONDecodeError, OSError):
                key = None
        out[p.stem] = key
    return out


def _build_diff(project: Project, snapshot_info: dict[str, Any] | None,
                before_finals: dict[str, str | None]) -> dict[str, Any]:
    """The step-5 structured diff: truth-text changes (gitops diff of the working
    tree against the pre-execution snapshot) + output changes (final content
    keys: which finals are new, which re-rendered). Degrades honestly when git is
    unavailable."""
    from ..core import gitops

    spec_diff = gitops.diff_text(project.root)  # None when git/repo unavailable
    after_finals = _final_keys(project)
    new = [n for n in after_finals if n not in before_finals]
    rerendered = [n for n in after_finals
                  if n in before_finals and after_finals[n] != before_finals[n]]

    diff: dict[str, Any] = {
        "spec": {
            "available": spec_diff is not None,
            "text": spec_diff or "",
        },
        "outputs": {
            "finals_before": sorted(before_finals),
            "finals_after": sorted(after_finals),
            "new_finals": new,
            "rerendered_finals": rerendered,
        },
    }
    # A per-shot compare of the two newest finals, when both exist and one moved.
    if len(after_finals) >= 2 and (new or rerendered):
        try:
            from .compare import compare_finals

            names = sorted(after_finals, key=lambda s: int(re.search(r"(\d+)", s).group(1)))
            cmp = compare_finals(project, names[-2], names[-1])
            diff["outputs"]["compare"] = {
                "a": cmp["a"]["name"], "b": cmp["b"]["name"],
                "summary": cmp.get("summary", {}),
                "changes": cmp.get("changes", []),
            }
        except Exception:
            pass  # compare is a bonus; its absence never breaks the diff
    return diff


def _record_action_failure(project: Project, action: dict, exc: ActionError,
                           actor: str) -> dict[str, Any]:
    from ..core.failures import Failure, record_failure

    step = {
        "build": "generate", "redo": "generate", "voice": "voice",
        "repair": "repair", "mixer": "compile", "captions": "captions",
        "packaging": "package", "snapshot": "check", "rollback": "check",
    }.get(action["type"], "generate")
    subject = exc.subject or action.get("shot") or action["type"]
    try:
        rec = record_failure(project, Failure(
            step=step, subject=subject,
            cause=f"director 动作失败({action['type']}):{' '.join(str(exc).split())}",
            evidence=exc.evidence, hint=exc.hint or "修好这一步后重新提案/执行",
            actor=actor, detail={"action": action}))
        return {"id": rec.get("id"), "step": rec.get("step"), "subject": subject,
                "cause": rec.get("cause"), "action": action}
    except Exception:
        return {"id": None, "step": step, "subject": subject,
                "cause": " ".join(str(exc).split()), "action": action}


# ---------------------------------------------------------- action dispatch

# Round AA (residual B, DECISIONS §9): execute() dispatches a MIX of action
# types. Four already take the cross-process ``runtime.buildlock.BuildLock``
# INSIDE their own engine function; wrapping them again here would
# self-deadlock (BuildLock is not reentrant — a second acquire in the same
# call always raises, even with zero external contention, exactly the
# no-double-acquire regression tests/test_write_locks.py already guards for
# GUI select/mixer). The rest write straight to disk (git commit, media
# repair sidecar + take file, captions.srt / rules.yaml, packaging.yaml) with
# NO lock of their own anywhere in their call chain — each is wrapped
# INDIVIDUALLY, right here at the dispatch site, so a director execute() run
# can no longer interleave with a concurrent CLI/GUI/MCP mutation of the same
# project mid-action. Table (derived by reading each handler's own engine
# call, not guessed):
#
#   action type | build_lock                              | why
#   ------------|------------------------------------------|---------------------------
#   build       | locked internally (graph.run_build)       | skip — would self-deadlock
#   redo        | locked internally (graph.redo_shot)       | skip — would self-deadlock
#   voice       | locked internally (graph.voice_batch)     | skip — would self-deadlock
#   mixer       | locked internally (mixer.apply_mixer)     | skip — would self-deadlock
#   repair      | NONE (media/repair_ops.py never locks)    | wrapped here
#   captions    | NONE (writes captions.srt/rules directly) | wrapped here
#   packaging   | NONE (media/packaging.py never locks)     | wrapped here
#   snapshot    | NONE (core/history.py never locks)        | wrapped here
#   rollback    | NONE (core/history.py never locks)        | wrapped here
_LOCKED_INTERNALLY = frozenset({"build", "redo", "voice", "mixer"})


def _run_action(project: Project, action: dict, actor: str, on_phase,
                *, agent_profile: str = "collaborative") -> dict[str, Any]:
    """Dispatch one validated action to its real engine entry point. Raises
    :class:`ActionError` on a clean failure (first-failure stop).

    See the type -> build_lock table above ``_LOCKED_INTERNALLY``: four
    action types already take the cross-process build lock inside their own
    engine function (never wrap those again here — self-deadlock); the rest
    are wrapped individually, right here, so every action this loop runs is
    now cross-process-safe exactly once."""
    atype = action["type"]
    handler = _DISPATCH.get(atype)
    if handler is None:  # unreachable — validation whitelisted the type
        raise ActionError(f"no handler for action type {atype!r}")
    # AI_IDE_16 §10: the build action is the paid-video path that honors the
    # keyframe spend gate under the unattended profile. Hand it the LIVE profile
    # on a runtime COPY (never mutate/persist the proposal's own action).
    if atype == "build":
        action = {**action, "_agent_profile": agent_profile}
    if atype in _LOCKED_INTERNALLY:
        return handler(project, action, actor, on_phase)

    from ..runtime.buildlock import BuildLocked
    from ..runtime.buildlock import build_lock as _build_lock

    try:
        with _build_lock(project.root, actor=actor):
            return handler(project, action, actor, on_phase)
    except BuildLocked as exc:
        raise ActionError(str(exc), subject=action.get("shot") or atype) from exc


def _do_build(project, action, actor, on_phase) -> dict[str, Any]:
    from .graph import WaitingUser, run_build

    try:
        # Pass agent_profile ONLY when it is the non-default unattended value, so
        # the collaborative/human path stays byte-identical to every existing
        # caller (and test double) of run_build.
        prof = action.get("_agent_profile", "collaborative")
        extra = {"agent_profile": prof} if prof != "collaborative" else {}
        result = run_build(
            project, target=action["target"], gen=action["gen"],
            regen_stale=action["regen_stale"], force=action["force"],
            actor=actor, assume_yes=True, on_phase=on_phase,  # confirmed → assume_yes
            **extra)
    except WaitingUser as exc:  # shouldn't fire under assume_yes; surfaced honestly
        raise ActionError(str(exc), subject="build") from exc
    if not result.ok:
        raise ActionError("build 未通过:" + " / ".join(result.errors[:4]),
                          subject="build",
                          evidence="\n".join(result.errors[:8]))
    return {"generated": result.generated, "skipped": result.skipped,
            "render": result.render_path, "qc_ok": result.qc_ok,
            "estimated_cost": result.estimated_cost}


def _do_redo(project, action, actor, on_phase) -> dict[str, Any]:
    from .graph import BuildError, WaitingUser, redo_shot

    try:
        takes = redo_shot(
            project, action["shot"],
            candidates=action.get("candidates"), provider=action.get("provider"),
            seed=action.get("seed"), from_take=action.get("from_take"),
            actor=actor, assume_yes=True)
    except (BuildError, WaitingUser) as exc:
        raise ActionError(str(exc), subject=action["shot"]) from exc
    except Exception as exc:  # ProviderFailure/NeedsHumanInput/MediaError
        raise ActionError(" ".join(str(exc).split()), subject=action["shot"]) from exc
    return {"shot": action["shot"], "takes": takes}


def _do_voice(project, action, actor, on_phase) -> dict[str, Any]:
    from .graph import voice_batch

    result = voice_batch(project, shots=[action["shot"]],
                         provider=action.get("provider"), actor=actor, assume_yes=True)
    failed = {f["shot"]: f["reason"] for f in result.failed}
    if action["shot"] in failed:
        raise ActionError(failed[action["shot"]], subject=action["shot"])
    return {"shot": action["shot"], "takes": result.takes.get(action["shot"], [])}


def _do_repair(project, action, actor, on_phase) -> dict[str, Any]:
    from ..media.ffmpeg import MediaError
    from ..media.repair_ops import (
        crop_pad_take,
        extend_take,
        retime_take,
        set_inout_take,
        trim_take,
    )

    shot = action["shot"]
    take = action.get("take")
    if take is None:
        try:
            take = project.load_shot(shot).status.selected_take
        except ProjectError as exc:
            raise ActionError(str(exc), subject=shot) from exc
    if not take:
        raise ActionError(f"{shot} 没有可修复的 take(先选用一条)", subject=shot)
    take = str(take)
    op = action["op"]
    try:
        if op == "retime":
            new = retime_take(project, shot, take, action["factor"])
        elif op == "extend":
            new = extend_take(project, shot, take, action["ms"], mode=action["mode"])
        elif op == "trim":
            new = trim_take(project, shot, take, action["ms"])
        elif op == "inout":
            new = set_inout_take(project, shot, take, action["in_ms"],
                                 action["out_ms"], mode=action["mode"])
        else:  # croppad
            new = crop_pad_take(project, shot, take, mode=action["mode"])
    except (MediaError, ProjectError, Exception) as exc:
        raise ActionError(" ".join(str(exc).split()), subject=shot) from exc
    append_event(project.root, actor, "repair",
                 {"shot": shot, "op": op, "source_take": take, "new_take": new.name})
    return {"shot": shot, "op": op, "source_take": take, "new_take": new.name}


def _do_mixer(project, action, actor, on_phase) -> dict[str, Any]:
    from .mixer import MixerError, apply_mixer

    try:
        report = apply_mixer(project, action["changes"], actor=actor)
    except (MixerError, ProjectError) as exc:
        raise ActionError(" ".join(str(exc).split()), subject="mixer") from exc
    return {"changed": report.get("changed"), "rebuild": report.get("rebuild")}


def _do_captions(project, action, actor, on_phase) -> dict[str, Any]:
    from ..core.yamlio import atomic_write_text
    from ..gui.captions_edit import _timeline_cues, cues_to_srt

    project.captions_dir.mkdir(parents=True, exist_ok=True)
    srt_path = project.captions_dir / "captions.srt"
    gen_path = project.captions_dir / "captions.generated.srt"
    rules = project.load_rules()
    if action["op"] == "save":
        was_manual = rules.captions.mode == "manual"
        flipped = False
        if not was_manual:
            if not gen_path.exists():
                if srt_path.exists():
                    atomic_write_text(gen_path, srt_path.read_text(encoding="utf-8"))
                else:
                    tcues = _timeline_cues(project)
                    if tcues:
                        atomic_write_text(gen_path, cues_to_srt(tcues))
            rules.captions.mode = "manual"
            project.save_rules(rules)
            flipped = True
        atomic_write_text(srt_path, cues_to_srt(action["cues"]))
        append_event(project.root, actor, "captions_edit",
                     {"cues": len(action["cues"]), "mode_flip": flipped, "mode": "manual"})
        return {"op": "save", "cues": len(action["cues"]), "flipped": flipped}
    # revert
    if rules.captions.mode != "manual":
        return {"op": "revert", "restored": False, "note": "已是自动字幕"}
    rules.captions.mode = "compiled"
    project.save_rules(rules)
    restored = False
    if gen_path.exists():
        atomic_write_text(srt_path, gen_path.read_text(encoding="utf-8"))
        restored = True
    else:
        tcues = _timeline_cues(project)
        if tcues:
            atomic_write_text(srt_path, cues_to_srt(tcues))
            restored = True
        else:
            srt_path.unlink(missing_ok=True)
    append_event(project.root, actor, "captions_revert",
                 {"restored": restored, "mode": "compiled"})
    return {"op": "revert", "restored": restored}


def _do_packaging(project, action, actor, on_phase) -> dict[str, Any]:
    if action["op"] == "package":
        from ..media.ffmpeg import MediaError
        from ..media.packaging import PackagingError, make_package

        try:
            result = make_package(project, force=action.get("force", False))
        except (PackagingError, MediaError, Exception) as exc:
            raise ActionError(" ".join(str(exc).split()), subject="package") from exc
        append_event(project.root, actor, "package",
                     {k: result.get(k) for k in ("cover", "teaser", "skipped")})
        return {"op": "package", "cover": result.get("cover"),
                "teaser": result.get("teaser"), "skipped": result.get("skipped")}
    # apply a packaging patch
    from pydantic import ValidationError

    from ..core.models import PackagingSpec

    base = project.load_packaging().model_dump()
    merged = dict(base)
    for k, v in action["patch"].items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            m = dict(merged[k]); m.update(v); merged[k] = m
        else:
            merged[k] = v
    try:
        spec = PackagingSpec.model_validate(merged)
    except ValidationError as exc:
        raise ActionError("packaging 校验失败:" + " ".join(str(exc).split())[:400],
                          subject="packaging") from exc
    project.save_packaging(spec)
    append_event(project.root, actor, "edit_packaging",
                 {"keys": sorted(action["patch"].keys())})
    return {"op": "apply", "changed": sorted(action["patch"].keys())}


def _do_snapshot(project, action, actor, on_phase) -> dict[str, Any]:
    from ..core.history import HistoryError, snapshot

    try:
        info = snapshot(project, action.get("label", ""))
    except HistoryError as exc:
        raise ActionError(str(exc), subject="snapshot") from exc
    return {"op": "snapshot", "sha": info.get("sha"), "clean": info.get("clean")}


def _do_rollback(project, action, actor, on_phase) -> dict[str, Any]:
    from ..core.history import HistoryError, rollback_file, rollback_shot

    try:
        if action["op"] == "shot":
            info = rollback_shot(project, action["shot"])
            return {"op": "shot", **info}
        info = rollback_file(project, action["path"], action.get("ref", "HEAD"))
        return {"op": "file", **info}
    except HistoryError as exc:
        raise ActionError(str(exc),
                          subject=action.get("shot") or action.get("path") or "rollback") from exc


_DISPATCH = {
    "build": _do_build,
    "redo": _do_redo,
    "voice": _do_voice,
    "repair": _do_repair,
    "mixer": _do_mixer,
    "captions": _do_captions,
    "packaging": _do_packaging,
    "snapshot": _do_snapshot,
    "rollback": _do_rollback,
}


# ------------------------------------------------------------- suggest_next


_REPAIR_HINT_RE = re.compile(r"repair --op (\w+) --shot (\S+)")


def suggest_next(project: Project) -> list[Suggestion]:
    """SUGGEST NEXT (step 6) — deterministic heuristics over the EXISTING signals
    only (no LLM, §0). Each suggestion carries a ready-made action payload an
    agent/user can pass straight back into :func:`propose`, closing the loop:

    - creation funnel pre-storyboard → 先把故事写完 (advisory, funnel next_action);
    - stale shots → 建议重做 (redo);
    - QC errors/warnings → 建议修复方案 (repair, when a QC item names an op);
    - missing final / cover → 建议生成 (build / package);
    - budget 近上限 → 提醒 (advisory, no action).

    Funnel awareness (round V, part C): when the creation funnel's current stage
    is still pre-storyboard (立意/梗概/节拍/剧本), the FIRST suggestion is that
    stage's next_action — finish the story before spending on shots. It carries
    the skill pointer in its text and no proposal action (a story edit is a human
    write, not a whitelisted director action). Additive: once the storyboard is
    done, no funnel suggestion is emitted and the existing signals lead.
    """
    out: list[Suggestion] = []

    # funnel first, but only pre-storyboard (advisory — a story edit is not a
    # whitelisted action). Never lets a funnel read error sink the suggestions.
    try:
        from .funnel import PRE_STORYBOARD, funnel_current

        cur = funnel_current(project)
        if cur and cur["id"] in PRE_STORYBOARD:
            out.append(Suggestion(
                kind="funnel",
                text=f"建议先完成{cur['cn']}:{cur['next_action']}",
                action=None))
    except Exception:
        pass

    try:
        statuses = evaluate_all(project)
    except Exception:
        statuses = []
    by_state: dict[str, list[str]] = {}
    for st in statuses:
        by_state.setdefault(st.state.value, []).append(st.shot_id)

    # missing shots → generate them
    if by_state.get(ShotState.MISSING.value):
        missing = by_state[ShotState.MISSING.value]
        out.append(Suggestion(
            kind="generate",
            text=f"有 {len(missing)} 个缺失镜头,建议生成:{', '.join(missing[:5])}"
                 + ("…" if len(missing) > 5 else ""),
            action={"type": "build", "target": "final", "gen": "missing"}))

    # needs_selection → a per-take HUMAN judgment (advisory; no proposal action)
    for sid in by_state.get(ShotState.NEEDS_SELECTION.value, []):
        out.append(Suggestion(kind="select", shot=sid,
                              text=f"{sid} 有多条 take 未选用 — 需人工挑选(manju select)",
                              action=None))

    # stale shots → redo
    for sid in by_state.get(ShotState.STALE.value, [])[:8]:
        out.append(Suggestion(kind="redo", shot=sid,
                              text=f"{sid} 待更新(上游已改)— 建议重做",
                              action={"type": "redo", "shot": sid}))

    # QC errors/warnings → repair, reading the last qc.json (no mutation)
    out.extend(_qc_suggestions(project))

    # DR02 WP4: derived assurance advisories from qc.json's assurance block
    # (rejected / unknown / stale) — advisory only, never a spend action.
    out.extend(_assurance_suggestions(project))

    # missing deliverables: final, then cover
    try:
        final = project.newest_final_path()
    except Exception:
        final = None
    if final is None and statuses and not by_state.get(ShotState.MISSING.value):
        out.append(Suggestion(kind="generate", text="还没有成片 final — 建议构建",
                              action={"type": "build", "target": "final"}))
    elif final is not None:
        cover = project.exports_dir / "packaging" / "cover.png"
        try:
            has_cover = cover.exists()
        except OSError:
            has_cover = True
        if not has_cover:
            out.append(Suggestion(kind="package", text="已有成片但没有封面 — 建议出封面/预告",
                                  action={"type": "packaging", "op": "package"}))

    # budget near the limit → 提醒 (advisory only)
    out.extend(_budget_suggestion(project))
    return out


def _qc_suggestions(project: Project) -> list[Suggestion]:
    import json

    qc_path = project.reports_dir / "qc.json"
    if not qc_path.exists():
        return []
    try:
        data = json.loads(qc_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    items = data.get("items", []) if isinstance(data, dict) else []
    out: list[Suggestion] = []
    n_err = sum(1 for i in items if i.get("level") == "error")
    seen: set[tuple[str, str]] = set()
    for it in items:
        if it.get("level") not in ("error", "warn"):
            continue
        m = _REPAIR_HINT_RE.search(it.get("suggestion") or "")
        if not m:
            continue
        op, shot = m.group(1), m.group(2)
        if op not in ("retime", "extend", "trim", "inout", "croppad"):
            continue
        if (op, shot) in seen or not _shot_exists(project, shot):
            continue
        seen.add((op, shot))
        action = {"type": "repair", "op": op, "shot": shot}
        if op in ("extend", "trim"):
            m2 = re.search(r"--ms (\d+)", it.get("suggestion") or "")
            if m2:
                action["ms"] = int(m2.group(1))
        m3 = re.search(r"--mode (\w+)", it.get("suggestion") or "")
        if m3:
            action["mode"] = m3.group(1)
        out.append(Suggestion(kind="repair", shot=shot,
                              text=f"质检建议修复 {shot}(修复方案:{op})", action=action))
    if n_err and not out:
        out.append(Suggestion(kind="repair",
                              text=f"质检有 {n_err} 处错误 — 见 reports/qc.md 逐条处理",
                              action=None))
    return out


def _assurance_suggestions(project: Project) -> list[Suggestion]:
    """DR02 WP4: turn the derived assurance block in reports/qc.json into
    ADVISORY next-steps (read-only, no mutation). Every suggestion is
    ``action=None`` — a rejected/unknown/stale shot is for a human/agent to
    triage (see the repair-loop skill), NEVER an auto-executed redo/propose with
    spend (the do-not-execute contract). Deterministic order (the block's shot
    order = Project.shot_ids()); an absent/old qc.json without the block yields
    zero suggestions."""
    import json

    qc_path = project.reports_dir / "qc.json"
    if not qc_path.exists():
        return []
    try:
        data = json.loads(qc_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    block = data.get("assurance") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return []

    out: list[Suggestion] = []
    for a in block.get("shots") or []:
        if not isinstance(a, dict):
            continue
        sid = (a.get("subject") or {}).get("id")
        state = a.get("assurance_state")
        if not sid:
            continue
        if state == "rejected":
            failed = a.get("failed_expectation_ids") or []
            ids = ", ".join(failed) if failed else "(blocker finding 阻断)"
            out.append(Suggestion(
                kind="repair", shot=sid,
                text=f"{sid} 验收未通过(rejected):未满足 expectation {ids} — 见 repair-loop",
                action=None))
        elif state == "unknown":
            unknown = a.get("unknown_expectation_ids") or []
            ids = ", ".join(unknown) if unknown else "(未观察)"
            out.append(Suggestion(
                kind="repair", shot=sid,
                text=f"{sid} 需要重新判读(uncertain/未观察):{ids} — "
                     f"manju qc brief --shots {sid}",
                action=None))
        elif state == "stale":
            out.append(Suggestion(
                kind="repair", shot=sid,
                text=f"{sid} 判读已过期(stale)— 建议 manju qc brief --shots {sid} 重新判读",
                action=None))
    return out


def _budget_suggestion(project: Project) -> list[Suggestion]:
    try:
        from .spend import spend_report

        report = spend_report(project)
    except Exception:
        return []
    limit = report.get("budget_limit")
    total = report.get("total") or 0.0
    if limit and total >= 0.8 * float(limit):
        cur = report.get("currency") or ""
        return [Suggestion(
            kind="budget",
            text=f"花费护栏提醒:已花 {total:g} / 上限 {float(limit):g} {cur} — 接近上限",
            action=None)]
    return []

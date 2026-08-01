"""`manju status` — the handover entry point (§10).

Either party takes over with one command: current phase, which shots are
missing/stale/awaiting approval, QC leftovers, cumulative spend, suggested
next step. 30 seconds to context.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.container import Project
from ..core.events import tail_events
from .stale import evaluate_all


def _qc_error_shots(project: Project) -> frozenset[str]:
    """Shot ids with level=error findings in the LAST qc report — read the
    same derived report the status surface already summarizes (advice for a
    human, never a build input). Unreadable/absent → empty, never a crash."""
    try:
        doc = json.loads((project.root / "reports" / "qc.json")
                         .read_text(encoding="utf-8"))
        return frozenset(
            str(item.get("shot"))
            for item in doc.get("items", [])
            if item.get("level") == "error" and item.get("shot"))
    except Exception:
        return frozenset()


def shot_next_action(project: Project, shot_id: str, *, state: str,
                     selected_take: str | None,
                     voice_state: str | None = None,
                     qc_error_shots: frozenset[str] = frozenset()) -> dict[str, str]:
    """Intuitiveness wave: ONE question, one answer — 这个镜头现在需要我做什么?

    A shot carries four-plus parallel state machines (build state, per-take
    verdicts, review annotations, QC findings, voice) and the owner's real
    task was diffing them in their head. This folds them into a single
    actionable sentence, most-blocking first, always naming the EXACT command
    — and the undo (`manju rollback shot`) wherever a choice can be wrong.
    Pure derivation over facts the surfaces already hold; no new state.

    Returns ``{"shot", "key", "action"}`` where ``key`` is the stable rung
    token (broken/missing/select/blocker/qc/voice/stale/review/ok) an agent
    can branch on, and ``action`` is the owner's sentence."""
    sid = shot_id

    def _r(key: str, action: str) -> dict[str, str]:
        return {"shot": sid, "key": key, "action": action}

    if state == "broken":
        return _r("broken",
                  f"修复:选中 take 的媒体不可用 — manju redo {sid} 重生成,"
                  f"或 manju select {sid} <take> 换选(选错可 manju rollback shot {sid})")
    if state == "missing":
        return _r("missing", f"生成:manju build 补齐(单镜 manju redo {sid})")
    if state == "needs_selection":
        return _r("select",
                  f"挑选:manju select {sid} <take>(对比看板 manju board --serve;"
                  f"选错可 manju rollback shot {sid})")

    # blocker annotations bound to the CURRENT selected media only — a STALE
    # binding (media since replaced) is the board's ⚠ 陈旧 business, never a nag.
    if selected_take:
        try:
            from ..core.hashing import hash_file  # process-cached

            shot = project.load_shot(sid)
            anns = [a for a in (shot.status.annotations or [])
                    if a.take == selected_take and a.severity == "blocker"]
            if anns:
                info = project.get_take(sid, selected_take)
                current = (hash_file(info.media_path)
                           if info is not None and info.media_path is not None
                           and info.media_path.is_file() else None)
                live = [a for a in anns if a.matches_media(current)]
                if live:
                    frames = ", ".join(f"f{a.frame}" for a in live if a.frame is not None)
                    where = f"(定位 {frames})" if frames else ""
                    return _r("blocker",
                              f"处理阻断批注:{len(live)} 条 blocker 指着当前媒体"
                              f"{where} — manju board --serve 查看,改后重选或重做")
        except Exception:
            pass  # annotation advice must never break the takeover surface

    if sid in qc_error_shots:
        return _r("qc", "QC 错误:manju repair,或按 reports/qc.md 处理后重跑 manju qc")

    # A take NEWER than the selected one is material nobody has decided about.
    # Takes are append-only and never auto-select over a human pick, which is
    # right — but it meant ingesting real footage for an already-selected shot
    # landed in TOTAL silence: `manju build` reported "final up-to-date" and the
    # film did not change, while `status` and `explain` both went on naming the
    # OLD take. The only trace was a `[pending=1]` counter in `ingest-batches`,
    # which nothing routes you to. So the shot that has an undecided take says
    # so here, ahead of the voice rung: a missing voice has many surfaces
    # already, and this had none. It is a decision, not a defect — the current
    # selection stays valid, and the sentence says so.
    # NOT when the shot is stale: a take minted under the old spec is stale
    # too, so "select the newer one" would be bad advice — the `stale` rung
    # below (redo, or keep the current pick) is the real answer there. This
    # rung is for a shot that is otherwise settled and simply has material
    # nobody has ruled on.
    if selected_take and state != "stale":
        try:
            newer = sorted(t.name for t in project.takes(sid)
                           if t.name > selected_take)
            if newer:
                num = newer[-1].split("_")[-1].lstrip("0") or newer[-1]
                return _r("newtake",
                          f"有更新的 take 未选用({', '.join(newer[-2:])},现选 "
                          f"{selected_take})— manju select {sid} {num} 选用,"
                          f"或不动(现选依然有效,§3 只增不改)")
        except Exception:
            pass  # take listing must never break the takeover surface

    if voice_state in ("missing", "stale"):
        return _r("voice", f"配音:manju voice {sid}")
    if state == "stale":
        # TRISURFACE F-05: right after `manju redo` this rung kept saying
        # "manju redo" — the exact command just run — because it never looked
        # for the candidate that redo minted from the CURRENT spec. Following
        # the printed advice looped forever; the real next action was select.
        # The key stays "stale" (agents keep their branch); only the sentence
        # changes, and only when a current-spec take actually exists.
        try:
            from ..core.spec import compute_spec_hash

            shot = project.load_shot(sid)
            bible = project.load_bible()
            # The SAME per-take freshness predicate build/stale.py applies to
            # the selected take (§4.3: judge a take by the SPEC_VERSION it was
            # generated under, with project_root so ref-image bytes count) —
            # a default-args hash here silently never matched the pipeline's
            # stamped value, and this branch never fired in the field.
            def _is_current(t) -> bool:
                sc = t.sidecar
                if sc is None or not sc.spec_hash:
                    return False
                return sc.spec_hash == compute_spec_hash(
                    shot, bible, version=sc.spec_version or 1,
                    project_root=project.root)

            fresh = sorted(
                t.name for t in project.takes(sid)
                if t.name != selected_take and _is_current(t))
            if fresh:
                num = fresh[-1].split("_")[-1].lstrip("0") or fresh[-1]
                return _r("stale",
                          f"spec 已变,且已有按当前 spec 重做的 take({fresh[-1]})"
                          f"— manju select {sid} {num} 选用,或保留现选"
                          f"(现选依然可用,§4.3)")
        except Exception:
            pass  # candidate probing must never break the takeover surface
        return _r("stale",
                  f"spec 已变:manju redo {sid} 重做,或保留现选(现选依然可用,§4.3)")

    try:
        review = getattr(project.load_shot(sid).status, "review", None)
    except Exception:
        review = None
    if review in ("needs_review", "in_progress") and selected_take:
        return _r("review", "审片:manju gui 的审片页好/弃,或看板批注后 通过")
    return _r("ok", "无需动作 ✅")


def next_actions(project: Project, *, statuses: Any = None,
                 voices: Any = None) -> list[dict[str, str]]:
    """The full per-shot pass for the takeover surfaces (`manju status` +
    JSON `todo`): one entry per shot whose answer is NOT 无需动作, in index
    order. Same precomputed-pass parameters as :func:`project_status`."""
    if statuses is None:
        statuses = evaluate_all(project)
    voice_by_shot: dict[str, str] = {}
    try:
        from .voice import VoiceState, evaluate_all_voices

        for vs in (voices if voices is not None else evaluate_all_voices(project)):
            if vs.state != VoiceState.NOT_NEEDED:
                voice_by_shot[vs.shot_id] = vs.state.value
    except Exception:
        pass
    qc_errors = _qc_error_shots(project)
    todo: list[dict[str, str]] = []
    for st in statuses:
        act = shot_next_action(
            project, st.shot_id, state=st.state.value,
            selected_take=st.selected_take,
            voice_state=voice_by_shot.get(st.shot_id),
            qc_error_shots=qc_errors)
        if act["key"] != "ok":
            todo.append(act)
    return todo


def _qualify_done(next_step: str, key: str, todo: list[dict[str, str]]) -> str:
    """Never print a bare 完成 ✅ directly above a list of 待办.

    The project-level next step and the per-shot todos answer different
    questions — "is the film buildable" vs "does any shot still want
    something" — but they print three lines apart, so 「下一步 完成 ✅」 over a
    dozen 待办 rows just reads as the tool contradicting itself. The verdict is
    unchanged (nothing BLOCKS the film); it now says what it is not counting."""
    if key != "done" or not todo:
        return next_step
    kinds: list[str] = []
    for label, k in (("配音", "voice"), ("新 take 待定", "newtake"),
                     ("待审", "review"), ("过期", "stale")):
        if any(t.get("key") == k for t in todo):
            kinds.append(label)
    what = "、".join(kinds) if kinds else "可选项"
    return f"完成 ✅ — 片子可出;另有 {len(todo)} 项非阻塞待办({what},见下)"


def _locale_voice_blocker(project: Project, lang: str) -> str | None:
    """Why ``manju build --lang <lang>`` would be refused, or None if it is
    genuinely runnable.

    The locale build fails closed when a line has a translation but no voice
    take (``有译文无配音`` — it refuses native audio under foreign subtitles).
    Whether that is FIXABLE by the owner right now depends on a TTS provider
    being configured, so the two cases get different sentences: with TTS, one
    command finishes it; without, the honest next step is configuring one (or
    dropping locale voice takes in by hand). Best-effort — any failure here
    returns None and the caller keeps its previous recommendation."""
    try:
        from ..core.locale import _current_tts_descriptor, locale_status

        st = locale_status(project, lang)["locales"].get(lang) or {}
        need = sorted(sid for sid, v in (st.get("voice") or {}).items()
                      if v in ("missing", "stale"))
        if not need:
            return None
        head = ", ".join(need[:3]) + ("…" if len(need) > 3 else "")
        if _current_tts_descriptor() is not None:
            return (f"{len(need)} 镜有译文无配音({head})— "
                    f"manju voice --missing --lang {lang} --yes 补齐后再出片")
        return (f"{len(need)} 镜有译文无配音({head}),且尚未配置 TTS —— "
                f"先配 provider(manju providers)再 manju voice --missing "
                f"--lang {lang} --yes,或手动放入 locale 配音 take")
    except Exception:
        return None


def project_status(project: Project, *, statuses: Any = None,
                   voices: Any = None) -> dict[str, Any]:
    """``statuses`` / ``voices`` (G2): a caller that already ran
    :func:`evaluate_all` / :func:`~manju.build.voice.evaluate_all_voices` (the
    GUI's ``build_state`` does, for the per-shot cards) may pass them so this
    does not recompute the same linear pass. Both default to ``None`` →
    computed here, so every standalone caller (``manju status``) is unchanged."""
    config = project.load_config()
    if statuses is None:
        statuses = evaluate_all(project)
    by_state: dict[str, list[str]] = {}
    notes: dict[str, str] = {}
    for st in statuses:
        by_state.setdefault(st.state.value, []).append(st.shot_id)
        if st.note:  # why-stale field evidence rides to the takeover surface
            notes[st.shot_id] = st.note

    # voice states (M3): mirror of the picture-side summary, keyed by the
    # voice_hash staleness anchor; not_needed shots are omitted for signal.
    voice_by_state: dict[str, list[str]] = {}
    try:
        from .voice import VoiceState, evaluate_all_voices

        voice_list = voices if voices is not None else evaluate_all_voices(project)
        for vs in voice_list:
            if vs.state != VoiceState.NOT_NEEDED:
                voice_by_state.setdefault(vs.state.value, []).append(vs.shot_id)
    except Exception:
        pass  # voice summary is advisory

    # goal 79: NEVER sum across currencies into one silently-mislabeled number
    # — group per currency first (sidecar_by_currency), then derive the
    # single-number total_cost/currency fields ONLY when there is exactly one
    # currency in play (kept for backward-compat callers); a genuinely mixed
    # ledger sets currency=None rather than whatever the LAST take happened
    # to carry (the actual bug: the old loop overwrote `currency` on every
    # iteration regardless of whether it matched the running total).
    sidecar_totals: dict[str | None, float] = {}
    for st in statuses:
        for take in project.takes(st.shot_id):
            remote = take.sidecar.remote
            if remote and remote.cost:
                sidecar_totals[remote.currency] = (
                    sidecar_totals.get(remote.currency, 0.0) + float(remote.cost))
    sidecar_by_currency = [
        {"currency": cur, "cost": round(cost, 6)}
        for cur, cost in sorted(sidecar_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]
    if len(sidecar_by_currency) == 1:
        total_cost = sidecar_by_currency[0]["cost"]
        currency = sidecar_by_currency[0]["currency"]
    elif sidecar_by_currency:  # genuinely mixed -> a raw sum is not a real number
        total_cost = sum(c["cost"] for c in sidecar_by_currency)
        currency = None
    else:
        total_cost = 0.0
        currency = config.budget.currency

    # Run ledger snapshot (§8.3), best-effort — the SQLite state is disposable
    # (§3), so any failure degrades to an "unavailable" marker, never an error.
    run_log_info: dict[str, Any] = {
        "runs": 0, "total_cost": 0.0, "currency": None, "by_currency": [],
        "note": "state.sqlite unavailable",
    }
    try:
        from ..runtime.state import RuntimeState

        with RuntimeState(project.root) as state:
            total, cur = state.total_cost()
            run_log_info = {
                "runs": state.count_runs(),  # COUNT(*), not len() over fetched rows
                "total_cost": float(total),
                "currency": cur,
                # goal 79: the honest per-currency breakdown — never merges
                # 10 CNY + 2 USD into one meaningless "12".
                "by_currency": state.total_cost_by_currency(),
                # in-flight cloud jobs: the resume-polling queue (§8.1) — the
                # one runtime-only state, so surface it at the takeover entry
                "pending_jobs": len(state.pending_jobs()),
            }
    except Exception:
        pass  # keep the "unavailable" marker above

    # The ledger is authoritative once populated; the sidecar-derived sum is the
    # §3 rebuild source and the fallback when the ledger has no runs yet.
    if run_log_info.get("runs"):
        total_cost = run_log_info["total_cost"]
        currency = run_log_info["currency"]
        sidecar_by_currency = run_log_info.get("by_currency") or sidecar_by_currency

    # Process build lock (R2): the takeover entry point must say when the OTHER
    # party (or a crashed run) holds the mutating build right now. Read-only
    # peek at the holder JSON — never acquires.
    build_lock_info: dict[str, Any] | None = None
    lock_path = project.runtime_dir / "build.lock"
    if lock_path.exists():
        try:
            holder = json.loads(lock_path.read_text(encoding="utf-8"))
            build_lock_info = holder if isinstance(holder, dict) else {}
        except (ValueError, OSError):
            build_lock_info = {"note": "lock file unreadable"}

    timeline = project.load_timeline()
    # Round W (issue #70): use the ONE numeric final resolver (final_v10 beats
    # final_v9) instead of a lexicographic sorted-glob, which used to pick
    # final_v9 as "latest" once a project passed 9 renders.
    final = project.newest_final_path()

    # FIX-A writes the .key.json sidecar only at render completion, so a latest
    # final lacking one is likely crash-truncated — say so instead of presenting
    # it as the project's finished 成片 (assessment 2.10-8, §10 takeover honesty).
    latest_final_note = None
    if final is not None and not final.with_suffix(".key.json").exists():
        latest_final_note = "final may be incomplete (no content-key sidecar; crashed render?)"
    # C13: surface locale finals at takeover so locale-only projects aren't "无成片".
    locale_finals: dict[str, str] = {}
    try:
        locales_root = project.final_dir / "locales"
        if locales_root.is_dir():
            # sorted(Path) folds case on Windows; this ordering decides which
            # locale the next-step message names first. POSIX-string order.
            for d in sorted(locales_root.iterdir(), key=lambda p: p.as_posix()):
                if not d.is_dir():
                    continue
                best = None
                best_n = -1
                for p in d.glob("final_v*.mp4"):
                    import re as _re
                    m = _re.match(r"final_v(\d+)$", p.stem)
                    if m and int(m.group(1)) > best_n:
                        best_n = int(m.group(1))
                        best = p
                if best is not None:
                    locale_finals[d.name] = project.relpath(best)
    except Exception:
        locale_finals = {}
    if final is None and locale_finals and not latest_final_note:
        latest_final_note = (
            "无 base 成片,已有 locale: "
            + ", ".join(f"{k}={v}" for k, v in locale_finals.items())
        )

    qc_summary = None
    qc_path = project.reports_dir / "qc.json"
    if qc_path.exists():
        try:
            qc_data = json.loads(qc_path.read_text(encoding="utf-8"))
            items = qc_data.get("items", [])
            from ..qc.checks import actionable_notes

            qc_summary = {
                "ok": qc_data.get("ok"),
                "errors": sum(1 for i in items if i.get("level") == "error"),
                "warnings": sum(1 for i in items if i.get("level") == "warn"),
                # 返工自由度波: info findings that carry a concrete command
                # (aspect re-author, audio beds…). Two numbers used to hide
                # them entirely behind a green 完成 ✅.
                "notes": actionable_notes(items),
            }
        except (ValueError, OSError):
            qc_summary = {"ok": None, "note": "qc.json unreadable"}

    # suggested next step, in build order. `next_step_key` is the STABLE
    # machine token for the same ladder (GPT-analysis wave): agents and GUIs
    # branch on the key, never on the Chinese text — rewording a sentence
    # must not break automation (the per-shot `todo` entries already follow
    # this key+text contract).
    if not statuses:
        # Every other rung names a command; this one — the FIRST thing a new
        # project shows — only named a directory, leaving the newcomer with
        # nothing to type. `manju new` already points at the guided funnel, so
        # say the same thing here rather than sending them back to the docs.
        next_step = ("创作阶段:还没有镜头 —— manju create 走引导漏斗,"
                     "或直接写 shots/*.yaml 再 manju check(引擎不编故事,§2)")
        next_step_key = "create_shots"
    elif by_state.get("missing"):
        next_step = f"manju build(补齐缺失镜头:{', '.join(by_state['missing'][:5])}…)" \
            if len(by_state.get("missing", [])) > 5 else \
            f"manju build(补齐缺失镜头:{', '.join(by_state['missing'])})"
        next_step_key = "build_missing"
    elif by_state.get("needs_selection"):
        next_step = f"manju select(待挑选:{', '.join(by_state['needs_selection'])})"
        next_step_key = "select"
    elif by_state.get("broken"):
        # The neighbouring rungs all name a command; this one described a task.
        # The per-shot 待办 already spells the remedy out, so the headline just
        # has to name the first one for the first broken shot.
        broken = by_state["broken"]
        next_step = (f"修复 broken 镜头:{', '.join(broken)} — "
                     f"manju redo {broken[0]} 重生成,或 manju select "
                     f"{broken[0]} <take> 换选")
        next_step_key = "fix_broken"
    elif timeline is None:
        next_step = "manju build(编译时间线并渲染)"
        next_step_key = "build_timeline"
    elif final is None:
        next_step = "manju build --target final"
        next_step_key = "build_final"
    elif qc_summary and qc_summary.get("errors"):
        next_step = "manju repair / 处理 reports/qc.md 中的错误"
        next_step_key = "fix_qc"
    elif by_state.get("stale"):
        next_step = "已可出片;stale 镜头可用 manju redo 重做"
        next_step_key = "redo_stale"
    else:
        # C49: base done but declared locales still missing finals → next is locale.
        missing_locale: list[str] = []
        try:
            from ..core.locale import list_locales

            for loc in list_locales(project):
                if loc not in locale_finals:
                    missing_locale.append(loc)
        except Exception:
            missing_locale = []
        if missing_locale:
            sample = ", ".join(missing_locale[:4])
            more = "…" if len(missing_locale) > 4 else ""
            # Only recommend the locale build if it can actually SUCCEED. A
            # locale whose lines are translated but whose voice takes are
            # missing is refused on purpose — the engine will not ship native
            # audio under foreign subtitles — so recommending that build as the
            # headline next step sent the owner to a command that fails 100% of
            # the time, and the GUI printed it in its most prominent slot. Name
            # the step that actually unblocks instead.
            blocked = _locale_voice_blocker(project, missing_locale[0])
            if blocked:
                next_step = f"locale {missing_locale[0]}:{blocked}"
                next_step_key = "locale_needs_voice"
            else:
                next_step = (
                    f"locale 成片未齐:{sample}{more} — "
                    f"manju build --lang {missing_locale[0]} --target final"
                )
                next_step_key = "build_locale"
        else:
            next_step = "完成 ✅"
            next_step_key = "done"

    # Preset label + advisory qc_focus (P3): purely a record the preset wrote
    # at `manju new` time; surfaced here so a takeover sees what to watch for.
    qc_focus = config.model_dump().get("qc_focus") or []

    return {
        "project": config.name,
        "preset": config.preset,
        "qc_focus": qc_focus,
        "mode": config.mode,
        "resolution": f"{config.width}x{config.height}@{config.fps}",
        "shots_total": len(statuses),
        "shots_by_state": by_state,
        "shot_notes": notes,
        "voice_by_state": voice_by_state,
        "timeline": {
            "exists": timeline is not None,
            "duration_ms": timeline.duration_ms if timeline else None,
            "mode": timeline.meta.mode if timeline else None,
        },
        "latest_final": project.relpath(final) if final else None,
        "latest_final_note": latest_final_note,
        "locale_finals": locale_finals,
        "qc": qc_summary,
        "total_cost": total_cost,
        "currency": currency,
        # goal 79: the honest breakdown — display ALL currencies present
        # (e.g. "12 CNY + 2 USD") instead of ever merging them into one number.
        "spend_by_currency": sidecar_by_currency,
        "run_log": run_log_info,
        "budget_limit": config.budget.limit,
        "recent_events": tail_events(project.root, 5),
        "next_step": _qualify_done(
            next_step, next_step_key, todo_items := next_actions(
                project, statuses=statuses, voices=voices)),
        "next_step_key": next_step_key,
        # Intuitiveness wave: the per-shot answers (ONE actionable sentence
        # per shot that needs anything) — additive; agents branch on `key`.
        "todo": todo_items,
        "build_lock": build_lock_info,
    }

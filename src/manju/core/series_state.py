"""AI_IDE_17 WP1/WP2/WP3 — three-layer series state, identity variants,
world-state source validation, continuity packets and season health.

THREE LAYERS, STRICTLY SEPARATED BY STORAGE LOCATION (contract §3 / ruling 1):

    canonical identity   the SERIES bible (``<series>/bible/*.yaml`` — the
                         existing sync-bible machinery owns it; nothing here
                         ever writes it)
    appearance/voice     ADDITIVE ``variants:`` lists ON canonical bible
    variants             entries (text source, human/agent authored) with the
                         contract §3 shape: variant_id / valid_from_episode /
                         valid_to_episode / changes / reference_roles /
                         voice_profile_ref. A pure checker diagnoses overlap /
                         contradiction / range problems; resolution for an
                         episode is deterministic.
    transient state      NEVER stored globally: episode-local source +
                         accepted observations (qc.production.
                         accepted_observed_state) only. The continuity packet
                         below is a DERIVED read model over them — it can
                         inform the next episode but never auto-promotes to
                         permanent facts (pin: nothing in this module mutates
                         any bible file).

Season health (WP3) is PURE AGGREGATION of each episode's CURRENT evidence —
release assessment (07C), unresolved submissions (DR06/P0 SQLite view), drift
trend (15), refs/variants gaps, locale versions, budget vs actual (ledger).
It re-derives nothing and a stale/blocked episode is NEVER masked by a
season-level ready (pin).

No SeriesDB, no global mutable asset store: every fact read here is text or an
existing rebuildable projection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .series import Series
from .yamlio import read_yaml

# world-state source file at the series root (text truth, human/agent edited;
# the engine only VALIDATES it — Skills/Director propose patches).
WORLD_STATE_FILE = "world_state.yaml"

# variant fields the §3 shape declares. `changes` keys are free-form (wardrobe/
# hair/injury/age_form/... — authored vocabulary, not enforced).
_VARIANT_KEYS = ("variant_id", "valid_from_episode", "valid_to_episode",
                 "changes", "reference_roles", "voice_profile_ref")

_LAYER_CANONICAL = "canonical"
_LAYER_VARIANT = "variant"
_LAYER_TRANSIENT = "transient"


# --------------------------------------------------------------- episode order


def episode_order(series: Series) -> dict[str, int]:
    """Registered episode id -> ordinal (series.yaml order — the ONE order
    authority). Unregistered on-disk episodes get no ordinal: a variant range
    naming them is a diagnostic, not a guess."""
    return {ref.id: i for i, ref in enumerate(series.load_config().episodes)}


# ------------------------------------------------------------------- variants


def variants_of(entry: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The additive ``variants:`` list on a canonical bible entry (empty when
    absent/malformed — a non-list is reported by the checker, not guessed at)."""
    if not isinstance(entry, dict):
        return []
    raw = entry.get("variants")
    return [v for v in raw if isinstance(v, dict)] if isinstance(raw, list) else []


def _range_indices(variant: dict[str, Any], order: dict[str, int]
                   ) -> tuple[int | None, int | None]:
    lo = order.get(str(variant.get("valid_from_episode") or ""))
    hi_id = variant.get("valid_to_episode")
    hi = order.get(str(hi_id)) if hi_id else (len(order) - 1 if order else None)
    return lo, hi


def variant_diagnostics(series: Series) -> list[dict[str, Any]]:
    """PURE checker over every canonical entry's variants (engine validates,
    never invents). Codes:

    - ``VARIANT_ID_MISSING`` / ``VARIANT_ID_DUPLICATE``
    - ``UNKNOWN_EPISODE``  — valid_from/to names an unregistered episode
    - ``RANGE_INVALID``    — valid_from after valid_to (series.yaml order)
    - ``VARIANT_OVERLAP``  — two variants of one subject share an episode
      (advisory when their ``changes`` touch disjoint fields)
    - ``VARIANT_CONTRADICTION`` — overlapping variants change the SAME field
      to different values (blocking-grade: resolution would be ambiguous)
    - ``UNKNOWN_VOICE_PROFILE_REF`` — voice_profile_ref names no bible entry
    """
    from .series import _load_series_bible_by_file

    order = episode_order(series)
    bible = _load_series_bible_by_file(series)
    all_ids = {aid for entries in bible.values() for aid in entries}
    diags: list[dict[str, Any]] = []

    for kind, entries in bible.items():
        for aid, entry in entries.items():
            variants = variants_of(entry)
            if not variants and isinstance(entry, dict) and "variants" in entry:
                diags.append({"code": "VARIANT_LIST_MALFORMED", "subject": aid,
                              "kind": kind, "detail": "variants 不是列表"})
            seen_ids: set[str] = set()
            spans: list[tuple[str, int, int, dict[str, Any]]] = []
            for v in variants:
                vid = str(v.get("variant_id") or "").strip()
                if not vid:
                    diags.append({"code": "VARIANT_ID_MISSING", "subject": aid,
                                  "kind": kind, "detail": "缺 variant_id"})
                    continue
                if vid in seen_ids:
                    diags.append({"code": "VARIANT_ID_DUPLICATE", "subject": aid,
                                  "kind": kind, "variant": vid})
                    continue
                seen_ids.add(vid)
                frm = str(v.get("valid_from_episode") or "")
                to = v.get("valid_to_episode")
                if frm not in order:
                    diags.append({"code": "UNKNOWN_EPISODE", "subject": aid,
                                  "kind": kind, "variant": vid,
                                  "detail": f"valid_from_episode {frm!r} 未注册"})
                    continue
                if to is not None and str(to) not in order:
                    diags.append({"code": "UNKNOWN_EPISODE", "subject": aid,
                                  "kind": kind, "variant": vid,
                                  "detail": f"valid_to_episode {to!r} 未注册"})
                    continue
                lo, hi = _range_indices(v, order)
                if lo is None or hi is None or lo > hi:
                    diags.append({"code": "RANGE_INVALID", "subject": aid,
                                  "kind": kind, "variant": vid,
                                  "detail": f"{frm} → {to}(顺序按 series.yaml)"})
                    continue
                vref = v.get("voice_profile_ref")
                if vref and str(vref) not in all_ids:
                    diags.append({"code": "UNKNOWN_VOICE_PROFILE_REF",
                                  "subject": aid, "kind": kind, "variant": vid,
                                  "detail": str(vref)})
                spans.append((vid, lo, hi, v))

            # pairwise overlap / contradiction over the VALID spans only.
            for i in range(len(spans)):
                for j in range(i + 1, len(spans)):
                    vid_a, lo_a, hi_a, va = spans[i]
                    vid_b, lo_b, hi_b, vb = spans[j]
                    if lo_a > hi_b or lo_b > hi_a:
                        continue  # disjoint
                    ca = va.get("changes") or {}
                    cb = vb.get("changes") or {}
                    conflict = sorted(
                        k for k in set(ca) & set(cb) if ca[k] != cb[k]
                    ) if isinstance(ca, dict) and isinstance(cb, dict) else []
                    diags.append({
                        "code": ("VARIANT_CONTRADICTION" if conflict
                                 else "VARIANT_OVERLAP"),
                        "subject": aid, "kind": kind,
                        "variants": [vid_a, vid_b],
                        "fields": conflict,
                    })
    return diags


def active_variant(entry: dict[str, Any] | None, eid: str,
                   order: dict[str, int]) -> dict[str, Any] | None:
    """The variant ACTIVE for ``eid``, resolved deterministically: among the
    variants whose [valid_from, valid_to] range contains the episode, the one
    with the LATEST valid_from wins (the most recently-started state); a tie
    breaks lexicographically on variant_id (stable). Contradictions are the
    CHECKER's business — resolution never guesses between conflicting values,
    it just picks the deterministic winner and the diagnostics say if that
    winner was ambiguous."""
    idx = order.get(eid)
    if idx is None:
        return None
    live: list[tuple[int, str, dict[str, Any]]] = []
    for v in variants_of(entry):
        vid = str(v.get("variant_id") or "")
        if not vid:
            continue
        lo, hi = _range_indices(v, order)
        if lo is None or hi is None or lo > hi:
            continue
        if lo <= idx <= hi:
            live.append((lo, vid, v))
    if not live:
        return None
    live.sort(key=lambda t: (t[0], t[1]))
    return live[-1][2]


def resolve_entry_for_episode(entry: dict[str, Any], eid: str,
                              order: dict[str, int]) -> dict[str, Any]:
    """A DERIVED per-episode view of one canonical entry: canonical fields with
    the active variant's ``changes`` overlaid. Read-only — the canonical entry
    is never mutated; the result is labelled by layer so canonical and variant
    facts can never be confused (§3), and TRANSIENT state is deliberately not
    representable here (it lives in episode source + accepted observations)."""
    variant = active_variant(entry, eid, order)
    canonical = {k: v for k, v in (entry or {}).items() if k != "variants"}
    effective = dict(canonical)
    overlay = {}
    if variant is not None and isinstance(variant.get("changes"), dict):
        overlay = dict(variant["changes"])
        effective.update(overlay)
    return {
        "episode": eid,
        "layer": _LAYER_VARIANT if variant is not None else _LAYER_CANONICAL,
        "canonical": canonical,
        "variant": None if variant is None else {
            "variant_id": variant.get("variant_id"),
            "valid_from_episode": variant.get("valid_from_episode"),
            "valid_to_episode": variant.get("valid_to_episode"),
            "changes": overlay,
            "reference_roles": list(variant.get("reference_roles") or []),
            "voice_profile_ref": variant.get("voice_profile_ref"),
        },
        "effective": effective,
        "note": "transient state 不在此视图(它只存在于分集 source 与 accepted "
                "observations,绝不写回 canonical)",
    }


# ----------------------------------------------------------------- world state


def world_state_path(series: Series) -> Path:
    return series.root / WORLD_STATE_FILE


def load_world_state(series: Series) -> list[dict[str, Any]]:
    """The explicit state-change entries (text truth; [] when absent). Shape
    per entry: ``{id, subject, change: {field: value…}, effective: {episode,
    scene?, shot?}, known: bool}``. Loading never validates — the checker does."""
    path = world_state_path(series)
    if not path.exists():
        return []
    data = read_yaml(path) or {}
    entries = data.get("entries") if isinstance(data, dict) else data
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def world_state_diagnostics(series: Series,
                            entries: list[dict[str, Any]] | None = None
                            ) -> list[dict[str, Any]]:
    """PURE validation of the world-state source — IDs, order, conflicts and
    effective range ONLY (contract §6: the engine never judges the story).

    Codes: ``ENTRY_ID_MISSING`` / ``ENTRY_ID_DUPLICATE`` /
    ``UNKNOWN_SUBJECT`` (no bible entry anywhere carries that id) /
    ``UNKNOWN_EPISODE`` (effective.episode unregistered) /
    ``STATE_CONFLICT`` (two entries set the SAME subject field to different
    values in the SAME effective episode — order cannot break the tie)."""
    from .series import _load_series_bible_by_file

    entries = load_world_state(series) if entries is None else entries
    order = episode_order(series)
    bible = _load_series_bible_by_file(series)
    known_ids = {aid for kind_entries in bible.values() for aid in kind_entries}
    diags: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_key: dict[tuple[str, str, str], Any] = {}

    for e in entries:
        entry_id = str(e.get("id") or "").strip()
        if not entry_id:
            diags.append({"code": "ENTRY_ID_MISSING", "detail": repr(e)[:80]})
            continue
        if entry_id in seen:
            diags.append({"code": "ENTRY_ID_DUPLICATE", "entry": entry_id})
            continue
        seen.add(entry_id)
        subject = str(e.get("subject") or "")
        if subject and subject not in known_ids:
            diags.append({"code": "UNKNOWN_SUBJECT", "entry": entry_id,
                          "subject": subject})
        eff = e.get("effective") or {}
        eid = str(eff.get("episode") or "") if isinstance(eff, dict) else ""
        if not eid or eid not in order:
            diags.append({"code": "UNKNOWN_EPISODE", "entry": entry_id,
                          "detail": f"effective.episode {eid!r} 未注册"})
            continue
        change = e.get("change") or {}
        if isinstance(change, dict):
            for field, value in change.items():
                key = (subject, str(field), eid)
                if key in by_key and by_key[key] != value:
                    diags.append({"code": "STATE_CONFLICT", "entry": entry_id,
                                  "subject": subject, "field": str(field),
                                  "episode": eid})
                by_key.setdefault(key, value)
    return diags


def world_state_at(series: Series, eid: str) -> dict[str, Any]:
    """DERIVED world view at (through) episode ``eid``: entries whose effective
    episode is at or before it, applied in (episode order, file order). Pure
    read — known/unknown flags carried verbatim, nothing promoted anywhere."""
    order = episode_order(series)
    idx = order.get(eid)
    state: dict[str, dict[str, Any]] = {}
    applied: list[str] = []
    if idx is None:
        return {"episode": eid, "state": {}, "applied_entries": [],
                "error": f"episode {eid!r} 未注册"}
    ranked = []
    for pos, e in enumerate(load_world_state(series)):
        eff = e.get("effective") or {}
        e_idx = order.get(str(eff.get("episode") or "")) if isinstance(eff, dict) else None
        if e_idx is None or e_idx > idx:
            continue
        ranked.append((e_idx, pos, e))
    for _e_idx, _pos, e in sorted(ranked, key=lambda t: (t[0], t[1])):
        subject = str(e.get("subject") or "")
        change = e.get("change") or {}
        if not subject or not isinstance(change, dict):
            continue
        slot = state.setdefault(subject, {})
        for field, value in change.items():
            slot[str(field)] = {"value": value,
                                "known": bool(e.get("known", True)),
                                "entry": e.get("id")}
        applied.append(str(e.get("id") or ""))
    return {"episode": eid, "state": state, "applied_entries": applied}


# ----------------------------------------------------------- continuity packet


def continuity_packet(series: Series, from_eid: str) -> dict[str, Any]:
    """The next-episode continuity packet DERIVED from episode ``from_eid``'s
    accepted observed endings (08_10_12C machinery, scoped across episodes) +
    the world state effective through it.

    A read model only (pin): nothing here writes any bible/source file, and the
    packet says so — accepted observations INFORM the next episode; promoting
    one to a permanent fact is a human/Skill source edit, never automatic."""
    from ..qc.production import accepted_observed_state

    packet: dict[str, Any] = {
        "schema": "manju.continuity-packet/v1",
        "from_episode": from_eid,
        "layer": _LAYER_TRANSIENT,
        "shots": [],
        "world_state": world_state_at(series, from_eid),
        "advisory": True,
        "note": "绑定式证据的派生视图:accepted ending 只作为下一集的参考,"
                "绝不自动升级为永久事实(不写回任何 bible/source)",
    }
    try:
        project = series.open_episode(from_eid)
    except Exception as exc:
        packet["error"] = str(exc)
        return packet
    try:
        shot_ids = project.shot_ids()
    except Exception:
        shot_ids = []
    for sid in shot_ids:
        try:
            state = accepted_observed_state(project, sid)
        except Exception as exc:
            packet["shots"].append({"shot": sid, "error": str(exc)})
            continue
        packet["shots"].append({
            "shot": sid,
            "status": state.get("status"),
            "authored_locks": state.get("authored_locks") or [],
            "observed_endpoint": state.get("observed_endpoint") or [],
        })
    ends = [s for s in packet["shots"] if s.get("observed_endpoint")]
    packet["accepted_ending"] = ends[-1] if ends else None
    return packet


# --------------------------------------------------------------- season health


def _episode_health(project: Any) -> dict[str, Any]:
    """One episode's CURRENT evidence, each block consumed verbatim from its
    single owner and degraded honestly (never re-derived, never fabricated)."""
    out: dict[str, Any] = {}

    # 07C release assessment — ready/blockers verbatim.
    try:
        from ..build.baseline import release_assessment

        ra = release_assessment(project)
        out["release"] = {
            "ready": bool(ra.get("ready")),
            "blockers": len(ra.get("blockers") or []),
            "blocker_codes": [b.get("code") for b in (ra.get("blockers") or [])][:8],
        }
    except Exception as exc:
        out["release"] = {"ready": False, "error": str(exc)[:160]}

    # unresolved paid submissions / incomplete runs — the P0 SQLite projection.
    # The DB is a REBUILDABLE projection: absent (deleted) is explainable, not
    # an error and never fabricated as zero (§10: 删除 SQLite 可解释).
    db = project.root / ".manju" / "state.sqlite"
    if not db.exists():
        out["unresolved_submissions"] = None
        out["unresolved_note"] = ("runtime DB 不存在(可重建投影;删除即无未决"
                                  "记录可读,不臆造为 0)")
    else:
        try:
            from ..runtime.state import RuntimeState

            out["unresolved_submissions"] = len(
                RuntimeState(project.root).unresolved_submissions())
        except Exception as exc:
            out["unresolved_submissions"] = None
            out["unresolved_note"] = str(exc)[:160]

    # 15 drift trend — verbatim summary, optional.
    try:
        from ..qc.production import drift_trend

        trend = drift_trend(project)
        out["drift"] = {k: trend.get(k) for k in ("status", "verdict", "trend")
                        if k in trend} or {"present": True}
    except Exception:
        out["drift"] = None

    # missing refs — refs_report verbatim.
    try:
        from .refs import refs_report

        refs = refs_report(project)
        out["refs_missing"] = len(refs.get("missing") or [])
    except Exception:
        out["refs_missing"] = None

    # locale / delivery language versions.
    try:
        from .locale import list_locales

        out["locales"] = list_locales(project)
    except Exception:
        out["locales"] = []

    # budget vs actual — the ledger's own report (raw totals + declared limit).
    try:
        from ..build.spend import spend_report

        spend = spend_report(project)
        out["budget"] = {
            "actual": float(spend.get("total") or 0.0),
            "currency": spend.get("currency"),
            "limit": spend.get("budget_limit"),
        }
    except Exception:
        out["budget"] = None
    return out


def season_health(series: Series) -> dict[str, Any]:
    """WP3 — the season dashboard: PURE aggregation of per-episode CURRENT
    evidence + the series-level variant/world-state diagnostics. Additive to
    ``manju series status --json`` (no new command group).

    THE PIN: ``season.ready`` is true ONLY when every registered episode is
    readable, release-ready, and free of unresolved submissions — a stale,
    blocked or unreadable episode always surfaces in ``not_ready`` and forces
    ``ready: false``. Season-level ready can never mask an episode."""
    config = series.load_config()
    episodes: list[dict[str, Any]] = []
    not_ready: list[dict[str, Any]] = []

    for ref in config.episodes:
        row: dict[str, Any] = {"id": ref.id, "title": ref.title}
        try:
            project = series.open_episode(ref.id)
            row.update(_episode_health(project))
        except Exception as exc:
            row["error"] = str(exc)
            episodes.append(row)
            not_ready.append({"id": ref.id, "reason": "unreadable episode"})
            continue
        episodes.append(row)
        release = row.get("release") or {}
        if not release.get("ready"):
            not_ready.append({
                "id": ref.id,
                "reason": "release not ready",
                "blockers": release.get("blocker_codes") or
                            ([release["error"]] if release.get("error") else []),
            })
        if row.get("unresolved_submissions"):
            not_ready.append({"id": ref.id, "reason": "unresolved paid submissions",
                              "count": row["unresolved_submissions"]})

    v_diags = variant_diagnostics(series)
    w_diags = world_state_diagnostics(series)

    return {
        "schema": "manju.season-health/v1",
        "series": config.name,
        "episodes": episodes,
        "episode_count": len(episodes),
        "variant_diagnostics": v_diags,
        "world_state_diagnostics": w_diags,
        "not_ready": not_ready,
        "ready": not not_ready,
        "note": "纯聚合:每块证据来自其唯一所有者(07C release assessment、P0 "
                "submissions 投影、15 drift、refs_report、locale、spend ledger);"
                "season ready 绝不掩盖任何一集的 stale/blocked/unreadable。",
    }

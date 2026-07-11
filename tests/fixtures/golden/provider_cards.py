"""AI_IDE_20B — Provider Regression Cards (contract §6), derived in TESTS.

A card is a PURE, derived view over AI_IDE_14's qualification machinery — the
stored qualification report (``providers.qualification.read_report``) re-derived
against the CURRENT declared facts (``declared_facts`` → ``qualification_state``)
— plus the manifest's declared error semantics. Nothing here is a new store:
deleting ``reports/providers/qualification/`` only loses history, and the card
then honestly shows the config floor.

Card fields (§6): qualification status · tested capability/scope · real error
semantics (declared + observed) · cost/latency · artifact probe · known
unsupported/untested · last profile/adapter digests · stale flag.

DELIBERATELY ABSENT: any aggregate "best provider" score/rank — routing stays
explicit policy (contract §6: 不做自动“最佳 Provider”总分). A self-test asserts
no such key ever appears.

This module lives under tests/ (addendum ruling: prefer deriving in tests — 0
production files); runtime code never imports it.
"""

from __future__ import annotations

from typing import Any

from manju.providers import qualification as Q

CARD_SCHEMA = "manju.golden_corpus.provider_card/20B"

# keys that must NEVER appear on a card (the no-total-score rule, §6).
FORBIDDEN_SCORE_KEYS = ("score", "total_score", "rank", "ranking", "best",
                        "overall_score", "rating")


def derive_card(project: Any, provider_id: str, capability: str, *,
                fixtures_dir=None) -> dict:
    """One provider capability's regression card. Pure read-only derivation:
    current declared facts + stored evidence → ``qualification_state`` (the same
    pure function `manju providers qualify` uses), then the §6 card projection.
    Deterministic for fixed inputs; no network, no clock."""
    declared = Q.declared_facts(provider_id, capability, fixtures_dir=fixtures_dir)
    report = Q.read_report(project, provider_id, capability)
    evidence = (report or {}).get("evidence")
    state = Q.qualification_state(provider_id, capability,
                                  evidence=evidence, declared=declared)

    from manju.providers.manifest import load_manifests

    manifests, _errors = load_manifests()
    manifest = manifests.get(provider_id)
    declared_caps = list(manifest.capabilities) if manifest is not None else []

    artifact = (evidence or {}).get("artifact")
    artifact_checks = (evidence or {}).get("artifact_checks")
    ev_cost = (evidence or {}).get("cost") or {}
    # the submission-state sequence rides the evidence_refs (one ref per
    # submission_state event, each carrying its "to" state).
    observed_states = [ref.get("to") for ref in
                       ((evidence or {}).get("evidence_refs") or [])
                       if isinstance(ref, dict) and ref.get("to")]

    error_semantics = {
        # DECLARED semantics come from the manifest (what the operator vouched);
        # OBSERVED come from real canary evidence (the submission event stream).
        "declared": {
            "content_rejected_when": (list(manifest.failure.content_rejected_when)
                                      if manifest is not None else []),
            "definite_rejection_statuses": (
                list(manifest.submit.definite_rejection_statuses or [])
                if manifest is not None and manifest.submit is not None else []),
        },
        "observed_submission_states": observed_states,
        "note": "observed 为 canary 证据中的真实提交状态序列;无证据时为空,"
                "绝不猜测远端语义。",
    }

    cost_latency = {
        "estimated_cost": (report or {}).get("estimated_cost"),
        "observed_cost": ev_cost.get("actual"),
        "currency": (report or {}).get("currency") or ev_cost.get("currency"),
        # latency is deliberately NOT recorded by the qualification machinery
        # (check date is audit metadata, never identity) — the card reports the
        # field honestly instead of inventing a number.
        "latency_ms": None,
        "latency_note": "qualification 不记录时延(时间只是审计元数据);"
                        "真实时延须由操作者的真实 canary 记录。",
    }

    tested = {
        "capability": capability,
        "mode": (report or {}).get("mode"),
        "transport": (report or {}).get("transport"),
        "fixture_version": state["bindings"].get("fixture_version"),
        "evidence_refs": state["bindings"].get("evidence_refs") or [],
    }

    untested = sorted(c for c in declared_caps if c != capability)

    return {
        "schema": CARD_SCHEMA,
        "provider_id": provider_id,
        "capability": capability,
        "qualification": {
            "state": state["state"],
            "level": state["level"],
            "stale": state["stale"],
            "blocked_reason": state["blocked_reason"],
            "reasons": list(state["reasons"]),
        },
        "tested": tested,
        # §6 已知不支持/未测试: declared-but-untested capabilities are NEVER
        # claimed supported (c14 test 14's untested-boundary rule, projected).
        "known_unsupported_or_untested": {
            "untested_capabilities": untested,
            "note": "未经 canary 的能力一律不声称支持(untested boundary)。",
        },
        "error_semantics": error_semantics,
        "cost_latency": cost_latency,
        "artifact_probe": {"artifact": artifact, "checks": artifact_checks},
        "digests": {
            "provider_profile_digest": state["bindings"].get("provider_profile_digest"),
            "adapter_semantic_digest": state["bindings"].get("adapter_semantic_digest"),
            "request_digest": state["bindings"].get("request_digest"),
            "response_schema_digest": state["bindings"].get("response_schema_digest"),
        },
        "derived_from": {
            "report_present": report is not None,
            "report_path": "reports/providers/qualification/"
                           f"{provider_id}__{capability}.json",
            "note": "派生视图:删除 qualification report 只丢历史,卡片回落到"
                    "config floor;卡片本身不是任何 build/路由输入。",
        },
        "routing_note": "无总分、无排名 —— routing 仍按显式策略(契约 §6)。",
    }


def derive_cards(project: Any, pairs: list[tuple[str, str]], *,
                 fixtures_dir=None) -> list[dict]:
    """Cards for a list of (provider_id, capability) pairs, in input order."""
    return [derive_card(project, pid, cap, fixtures_dir=fixtures_dir)
            for pid, cap in pairs]


def assert_no_score(card: dict) -> None:
    """Raise AssertionError if any forbidden aggregate-score key appears
    anywhere in the card (the §6 no-best-provider rule as an executable check)."""
    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and k.lower() in FORBIDDEN_SCORE_KEYS:
                    raise AssertionError(f"forbidden score key {k!r} at {path}")
                walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]")
    walk(card)

"""Model-independent routing strategies (goal item 9).

The §8.4 fallback chain answers "if this provider fails, what next?". Routing
answers the question one level up: "for THIS shot, which provider should we
reach for first?" — as data the director edits, never code.

Two files, project wins, both optional::

    <project>/timeline/routing.yaml     # per-project
    ~/.manju/routing.yaml               # user default

Shape (litellm-style named strategies, but model-independent)::

    strategy: cheapest            # which named strategy is active
    strategies:                   # user-defined (built-ins need no definition)
      my_rules:
        rules:
          - match: {shot_size: close_up, has_dialogue: true}
            use: talking_head_api
          - match: {provider_capability: image_to_video}
            use: comfyui
        else: fallback            # provider-id | fallback | cheapest | local | quality

Built-in strategies (shipped as DATA, see :data:`BUILTIN_STRATEGIES`):
``default`` (today's behaviour), ``local_only`` (never cloud), ``cheapest``
(per-second-cheapest capable manifest), ``quality_first`` (a priority list the
user edits).

Human-readable TIERS (goal item 15). A routing.yaml may also carry an optional
``tiers:`` mapping — a director-facing vocabulary the rules read::

    tiers:
      draft:    {description: 草稿:先快后好,便宜看个大概, use: [caption_card]}
      review:   {description: 审片:够看清就行,      use: [comfyui]}
      key_shot: {description: 关键镜头:优先质量,     use: [runway, comfyui]}

A shot reaches a tier through a dedicated **additive** ``ShotSpec.tier`` field
(``tier: key_shot`` in the shot YAML). That path is chosen deliberately over
``generation.params.tier``: :func:`manju.core.spec.spec_payload` hashes the whole
``generation`` block but NOT top-level ``tier``, so tagging a shot's tier is a
pure routing choice that never restages the picture or marks an existing take
stale. Rules may also match it explicitly (``match: {tier: key_shot}``).

Resolution order (the pin): ``shot.generation.provider`` (explicit always wins)
> the active strategy's first matching rule > the shot's ``tiers:`` mapping (a
tier is an implicit rule keyed on ``shot.tier``) > the ``else`` selector > the
§8.4 fallback chain (always the safety net). Disabled providers are skipped, the
reason recorded. When NEITHER routing file exists the whole module is bypassed
by the registry, and with no ``tiers:`` / no tier match keys the resolution is
byte-identical to §8.4.

Build MODES (goal item 14) bias resolution without touching the pin: the caller
passes ``else_bias`` (from ``manju build --mode``) which replaces ONLY the
*neutral* ``fallback`` else — an explicit provider, a fired rule, a fired tier
and an opinionated strategy ``else`` are all left untouched.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.yamlio import read_yaml

# ---------------------------------------------------------------- vocabulary

# match keys evaluated against the SHOT (+ its generation params via params.<k>)
KNOWN_MATCH_KEYS = {
    "shot_size",
    "camera_motion",
    "has_dialogue",
    "duration_gt",
    "duration_lt",
    "characters_include",
    "scene",
    "provider_capability",
    "tier",  # goal 15: the shot's human-readable routing tier (ShotSpec.tier)
}

# `else:` selectors that are meta-behaviours rather than a literal provider id
META_ELSE = {"fallback", "cheapest", "local", "quality"}

# Built-in strategies, shipped as plain data so they are the same thing a user
# would write. A user routing.yaml may redefine any of them (project/user file
# entries override the built-in of the same name field-by-field).
BUILTIN_STRATEGIES: dict[str, dict[str, Any]] = {
    "default": {"rules": [], "else": "fallback"},
    "local_only": {"rules": [], "else": "local"},
    "cheapest": {"rules": [], "else": "cheapest"},
    "quality_first": {"rules": [], "else": "quality", "priority": []},
}


class RoutingError(ValueError):
    """A malformed routing.yaml or an unknown match key (§ debuggability):
    surfaced by `manju route list/explain` and `providers check`, never a
    silent false."""


# --------------------------------------------------------------- file loading


@dataclass
class RoutingConfig:
    strategy: str = "default"
    strategies: dict[str, dict[str, Any]] = field(default_factory=dict)
    # goal 15: human-readable tiers (name -> {description, use: [provider ids]}).
    tiers: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)  # "user" / "project", low→high


def user_routing_path() -> Path:
    """~/.manju/routing.yaml, honouring the same test override as manifests:
    ``MANJU_ROUTING`` (explicit file) or the parent of ``MANJU_PROVIDERS_DIR``."""
    override = os.environ.get("MANJU_ROUTING")
    if override:
        return Path(override)
    providers_dir = os.environ.get("MANJU_PROVIDERS_DIR")
    base = Path(providers_dir).parent if providers_dir else Path.home() / ".manju"
    return base / "routing.yaml"


def project_routing_path(project) -> Path:
    return Path(project.root) / "timeline" / "routing.yaml"


def _read_or_none(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = read_yaml(path) or {}
    except (OSError, ValueError) as exc:
        raise RoutingError(f"{path}: cannot read routing file ({exc})") from exc
    if not isinstance(data, dict):
        raise RoutingError(f"{path}: routing file must be a mapping, got {type(data).__name__}")
    return data


def load_routing(project) -> RoutingConfig | None:
    """Merge user then project routing files; project wins. ``None`` when
    neither exists (the registry then keeps the byte-identical §8.4 path)."""
    user = _read_or_none(user_routing_path())
    proj = _read_or_none(project_routing_path(project)) if project is not None else None
    if user is None and proj is None:
        return None
    cfg = RoutingConfig()
    for label, data in (("user", user), ("project", proj)):  # project last → wins
        if not data:
            continue
        cfg.sources.append(label)
        if "strategy" in data:
            cfg.strategy = str(data["strategy"])
        for name, sdef in (data.get("strategies") or {}).items():
            cfg.strategies[name] = sdef
        for name, tdef in (data.get("tiers") or {}).items():  # project tier wins
            cfg.tiers[name] = tdef
    return cfg


def _default_config() -> RoutingConfig:
    return RoutingConfig(strategy="default", strategies={}, sources=[])


# ------------------------------------------------------------ strategy shapes


def _validate_match_keys(match: dict[str, Any], where: str) -> None:
    for key in match:
        if key in KNOWN_MATCH_KEYS or key.startswith("params."):
            continue
        raise RoutingError(
            f"{where}: unknown match key {key!r} "
            f"(known: {sorted(KNOWN_MATCH_KEYS)} or params.<k>)"
        )


def _normalize_strategy(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RoutingError(f"strategy {name!r} must be a mapping")
    rules = raw.get("rules", []) or []
    if not isinstance(rules, list):
        raise RoutingError(f"strategy {name!r}: rules must be a list")
    norm_rules: list[dict[str, Any]] = []
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise RoutingError(f"strategy {name!r} rule {i}: must be a mapping")
        match = rule.get("match", {}) or {}
        if not isinstance(match, dict):
            raise RoutingError(f"strategy {name!r} rule {i}: match must be a mapping")
        _validate_match_keys(match, f"strategy {name!r} rule {i}")
        use = rule.get("use")
        if not use:
            raise RoutingError(f"strategy {name!r} rule {i}: needs a `use: <provider-id>`")
        norm_rules.append({"match": match, "use": str(use)})
    else_sel = str(raw.get("else", "fallback"))
    priority = raw.get("priority", []) or []
    if not isinstance(priority, list):
        raise RoutingError(f"strategy {name!r}: priority must be a list")
    return {"rules": norm_rules, "else": else_sel, "priority": [str(p) for p in priority]}


def _normalize_tier(name: str, raw: Any) -> dict[str, Any]:
    """A tier is ``{description: 中文一句话, use: [provider priority list]}``.
    Both keys optional; ``use`` must be a list. Malformed → RoutingError (surfaced
    by the route commands, never a silent false)."""
    if not isinstance(raw, dict):
        raise RoutingError(f"tier {name!r} must be a mapping with description/use")
    use = raw.get("use", []) or []
    if not isinstance(use, list):
        raise RoutingError(f"tier {name!r}: use must be a list of provider ids")
    return {"description": str(raw.get("description", "") or ""),
            "use": [str(p) for p in use]}


def tier_def(config: RoutingConfig, name: str) -> dict[str, Any] | None:
    """The normalized tier definition for ``name``, or ``None`` if undefined."""
    if name in config.tiers:
        return _normalize_tier(name, config.tiers[name])
    return None


def _strategy_def(config: RoutingConfig, name: str) -> dict[str, Any]:
    if name in config.strategies:
        base = dict(BUILTIN_STRATEGIES.get(name, {}))
        base.update(config.strategies[name])  # user/project override built-in of same name
        return _normalize_strategy(name, base)
    if name in BUILTIN_STRATEGIES:
        return _normalize_strategy(name, dict(BUILTIN_STRATEGIES[name]))
    available = sorted(set(BUILTIN_STRATEGIES) | set(config.strategies))
    raise RoutingError(f"unknown strategy {name!r}; available: {available}")


# ---------------------------------------------------------- provider catalogue


@dataclass
class _ProviderView:
    id: str
    kind: str  # "local" | "cloud"
    capabilities: list[str]
    per_second: float
    per_call: float
    disabled: bool
    exists: bool
    adapter: str = ""  # goal 30: needed to mirror the #29 candidates-clamp when pricing
    # DR04: the duration cap generic_cloud enforces at submit — routing reads it
    # to skip a structurally-incompatible candidate BEFORE the paid path.
    max_duration_ms: int | None = None


def _catalog() -> dict[str, _ProviderView]:
    """Every provider Manju knows about, disabled ones included, as a flat view
    the selectors read (kind for local_only, per_second for cheapest, disabled
    for the skip-reason ledger, capabilities for 'capable', max_duration_ms for
    the DR04 compatibility skip).

    DR04: a THIN consumer of :func:`providers.catalog.iter_provider_descriptors`
    — the provider FACTS now come from that one shared module (routing, the CLI
    and preflight all read the same descriptors), and ``_ProviderView`` stays as
    routing's internal adapter. The resulting view is byte-identical to the old
    hand-rolled scan (pinned by test_providers_routing + test_dr04_characterization)."""
    from .catalog import iter_provider_descriptors

    descriptors, _errors = iter_provider_descriptors()
    return {
        d.provider_id: _ProviderView(
            id=d.provider_id,
            kind=d.kind,
            capabilities=list(d.capabilities),
            per_second=d.per_second,
            per_call=d.per_call,
            disabled=not d.enabled,
            exists=d.exists,
            adapter=d.adapter,
            max_duration_ms=d.max_duration_ms,
        )
        for d in descriptors
    }


def _incompatible_skip(view: "_ProviderView", shot) -> str | None:
    """DR04 fail-earlier: the reason ``view`` is structurally INCOMPATIBLE with
    this shot on an EXECUTABLE fact the submit path also enforces — today the
    duration cap, via the SAME rule ``generic_cloud._enforce_limits`` applies
    (``providers.preflight.duration_exceeds_limit``). Returns a visible skip
    reason, or ``None``.

    Scope note (documented, not a gap): reference-count shortfalls are OMISSIONS
    (never a hard skip — the shot still generates), and capability is already the
    filter in :func:`_capable_sorted`; so neither belongs here. Only an
    INCOMPATIBLE-level fact that would otherwise be caught (and degraded) at
    submit is surfaced as a routing skip."""
    d = _duration_s(shot)
    if d is None:  # "auto" duration → nothing to check yet
        return None
    from .preflight import duration_exceeds_limit

    duration_ms = int(round(d * 1000))
    if duration_exceeds_limit(view.max_duration_ms, duration_ms):
        return (f"incompatible: shot needs {duration_ms}ms but {view.id} caps at "
                f"max_duration_ms {view.max_duration_ms}ms")
    return None


def explicit_pin_incompatibility(pid: str, req: Any) -> str | None:
    """DR04 fail-earlier for an EXPLICIT provider pin: the reason ``pid`` is
    structurally incompatible with THIS request (duration over its
    ``max_duration_ms`` — the SAME fact generic_cloud enforces at submit), or
    ``None``. The registry raises on a non-None result so an explicit pin fails
    BEFORE submit instead of degrading to the fallback chain (never silently
    replaced). Defensive: any lookup hiccup returns ``None`` — the submit-time
    guard remains the final defense."""
    try:
        from .catalog import iter_provider_descriptors
        from .preflight import duration_exceeds_limit

        descriptors, _ = iter_provider_descriptors()
        d = next((x for x in descriptors if x.provider_id == pid), None)
        if d is None:
            return None
        duration_ms = getattr(req, "duration_ms", None)
        if duration_exceeds_limit(d.max_duration_ms, duration_ms):
            return (f"needs {duration_ms}ms but its max_duration_ms is "
                    f"{d.max_duration_ms}ms — split the shot or pick another provider")
    except Exception:
        return None
    return None


def _provider_price(view: "_ProviderView", duration_s: float, candidates: int) -> float:
    """The price THIS provider would actually be charged for one shot (goal
    30): ``per_call + per_second × expected_duration``, times candidates —
    the same shape ``providers.manifest.estimate_cost`` uses, not just
    ``per_second`` alone (a short clip on a high-per-call provider can easily
    beat a low-per-second one). Mirrors the #29 clamp: generic_cloud's
    submit/poll/download path always returns exactly ONE result per job, so
    it is priced at 1 candidate regardless of what the shot requests — the
    same honest number the estimator and the ledger fallback show."""
    from .manifest import GENERIC_ADAPTER

    eff_candidates = 1 if view.adapter == GENERIC_ADAPTER else max(1, int(candidates or 1))
    return view.per_call + view.per_second * max(0.0, duration_s) * eff_candidates


def _shot_price_inputs(project: Any, shot: Any) -> tuple[float, int]:
    """(expected_duration_s, candidates) for :func:`_provider_price` — the SAME
    target-duration helper ``build.graph`` uses for its own estimate, so
    ``cheapest`` ranks providers on the price the estimator/ledger will
    actually show. Degrades to the shot's raw ``duration`` (0 for "auto") when
    the build module or project rules are unavailable — advisory only, a
    resolution failure here must never break routing."""
    candidates = max(1, int(getattr(shot.generation, "candidates", 1) or 1))
    duration_s = 0.0
    d = getattr(shot, "duration", None)
    if isinstance(d, (int, float)) and not isinstance(d, bool):
        duration_s = float(d)
    if project is not None:
        try:
            from ..build.graph import _target_duration_ms

            rules = project.load_rules()
            duration_s = _target_duration_ms(project, shot, rules) / 1000.0
        except Exception:
            pass
    return duration_s, candidates


# ------------------------------------------------------------- match evaluation


def _duration_s(shot) -> float | None:
    d = shot.duration
    return float(d) if isinstance(d, (int, float)) and not isinstance(d, bool) else None


def _match_one(key: str, expected: Any, shot) -> bool:
    if key == "shot_size":
        return shot.camera.shot_size == expected
    if key == "camera_motion":
        return shot.camera.movement == expected
    if key == "has_dialogue":
        return bool(str(shot.dialogue.text).strip()) == bool(expected)
    if key == "scene":
        return shot.scene == expected
    if key == "duration_gt":
        d = _duration_s(shot)
        return d is not None and d > float(expected)
    if key == "duration_lt":
        d = _duration_s(shot)
        return d is not None and d < float(expected)
    if key == "characters_include":
        wanted = expected if isinstance(expected, list) else [expected]
        return any(c in shot.characters for c in wanted)
    if key == "provider_capability":
        return expected in shot.generation.fallback
    if key == "tier":
        return getattr(shot, "tier", None) == expected
    if key.startswith("params."):
        return shot.generation.params.get(key[len("params."):]) == expected
    raise RoutingError(
        f"unknown match key {key!r} (known: {sorted(KNOWN_MATCH_KEYS)} or params.<k>)"
    )


def _match(match: dict[str, Any], shot) -> bool:
    return all(_match_one(k, v, shot) for k, v in match.items())


def _why_no_match(match: dict[str, Any], shot) -> str:
    for k, v in match.items():
        if not _match_one(k, v, shot):  # may raise RoutingError on an unknown key
            return f"match {k}={v!r} did not hold"
    return "no rule condition held"


# ------------------------------------------------------------------- resolving


@dataclass
class Resolution:
    shot_id: str
    strategy: str
    order: list[str]
    fired_rule: dict[str, Any] | None
    else_selector: str
    skipped: list[dict[str, Any]]
    # goal 15: the tier that supplied the head, when a `tiers:` mapping fired
    # (name + description + resolved use list); None otherwise.
    fired_tier: dict[str, Any] | None = None

    @property
    def chosen(self) -> str | None:
        return self.order[0] if self.order else None


def _capable_sorted(cat: dict[str, _ProviderView], shot, *, kind: str | None = None,
                    by_cost: bool = False, project: Any = None,
                    skipped: list[dict[str, Any]] | None = None) -> list[str]:
    """Enabled providers advertising a capability this shot's pipeline calls
    for (its generation.fallback steps), optionally filtered to one kind and
    ordered cheapest-first.

    Goal 30: "cheapest" ranks by the REAL per-shot price — ``per_call +
    per_second × expected_duration`` (candidates-clamped per #29) — not just
    ``per_second``, so a high-per-call/low-per-second provider is no longer
    wrongly preferred for a short clip over the opposite shape.

    DR04 fail-earlier: a candidate that is structurally incompatible with this
    shot (duration over its cap) is dropped with a VISIBLE reason appended to
    ``skipped`` — so ``cheapest``/``local``/``quality`` only ever compare
    candidates that could actually run."""
    needs = set(shot.generation.fallback)
    cands: list[_ProviderView] = []
    for v in cat.values():
        if not (v.exists and not v.disabled
                and (kind is None or v.kind == kind)
                and (set(v.capabilities) & needs)):
            continue
        reason = _incompatible_skip(v, shot)
        if reason is not None:
            if skipped is not None:
                skipped.append({"provider": v.id, "reason": reason})
            continue
        cands.append(v)
    if by_cost:
        duration_s, candidates = _shot_price_inputs(project, shot)
        cands.sort(key=lambda v: (_provider_price(v, duration_s, candidates), v.id))
    else:
        cands.sort(key=lambda v: v.id)
    return [v.id for v in cands]


def resolve(project, shot, config: RoutingConfig | None = None, *,
            else_bias: str | None = None) -> Resolution:
    """Resolve the ordered provider list for one shot under the active strategy.

    ``else_bias`` (goal 14, build modes) replaces ONLY the *neutral* ``fallback``
    else selector — so a mode never re-routes a shot an explicit provider, a rule
    or a tier already claimed, and never overrides an opinionated strategy whose
    else the user deliberately set to something other than ``fallback``."""
    from .registry import fallback_chain

    if config is None:
        config = load_routing(project) or _default_config()
    cat = _catalog()
    strat = _strategy_def(config, config.strategy)
    # A build mode biases ONLY the neutral fallback else — an opinionated strategy
    # else (local/cheapest/quality/literal) the user chose is left untouched.
    else_sel = else_bias if (else_bias is not None and strat["else"] == "fallback") \
        else strat["else"]
    order: list[str] = []
    skipped: list[dict[str, Any]] = []

    def _record_skip(entry: dict[str, Any]) -> None:
        if entry not in skipped:  # dedup: a candidate reached from >1 path skips once
            skipped.append(entry)

    def add(pid: str, *, explicit: bool = False) -> None:
        if pid in order:  # already chosen upstream — never re-evaluate/duplicate
            return
        view = cat.get(pid)
        if view is None or not view.exists:
            _record_skip({"provider": pid, "reason": "not registered / no manifest"})
            return
        if view.disabled:
            _record_skip({"provider": pid,
                          "reason": f"disabled (run: manju providers enable {pid})"})
            return
        # DR04 fail-earlier: a rule/tier/else/fallback candidate that is
        # structurally incompatible is skipped with a visible reason (its
        # RELATIVE position in whatever list it came from is otherwise untouched);
        # an EXPLICIT pin is kept in the order (byte-identical) so the registry's
        # explicit-pin guard raises on it BEFORE submit rather than the fallback
        # silently replacing it.
        reason = _incompatible_skip(view, shot)
        if reason is not None:
            _record_skip({"provider": pid, "reason": reason})
            if not explicit:
                return
        order.append(pid)

    # 1. explicit provider always wins (a disabled one is a hard build error
    #    raised in the registry before we get here; recorded as a skip in explain)
    if shot.generation.provider:
        add(shot.generation.provider, explicit=True)

    # 2. the active strategy's FIRST matching rule
    fired: dict[str, Any] | None = None
    for i, rule in enumerate(strat["rules"]):
        if _match(rule["match"], shot):
            fired = {"index": i, "match": rule["match"], "use": rule["use"]}
            add(rule["use"])
            break
        skipped.append({"rule": i, "reason": _why_no_match(rule["match"], shot)})

    # 2t. the shot's TIER (goal 15) — an implicit rule keyed on shot.tier, tried
    #     only when no explicit strategy rule fired. Its `use` list is a priority.
    fired_tier: dict[str, Any] | None = None
    if fired is None:
        shot_tier = getattr(shot, "tier", None)
        if shot_tier:
            tdef = tier_def(config, shot_tier)
            if tdef is not None:
                fired_tier = {"name": shot_tier, **tdef}
                for pid in tdef["use"]:
                    add(pid)

    # 3. the `else` selector — only when neither a rule nor a tier fired
    if fired is None and fired_tier is None:
        if else_sel == "fallback":
            pass  # the safety net below is exactly this
        elif else_sel == "local":
            for pid in _capable_sorted(cat, shot, kind="local", skipped=skipped):
                add(pid)
        elif else_sel == "cheapest":
            for pid in _capable_sorted(cat, shot, by_cost=True, project=project,
                                       skipped=skipped):
                add(pid)
        elif else_sel == "quality":
            for pid in strat["priority"]:
                add(pid)
        else:  # a literal provider id
            add(else_sel)

    # 4. §8.4 fallback chain — always the safety net (fallback_chain already
    #    drops disabled providers). DR04 keeps this byte-identical: "fallback
    #    ORDER unchanged" is a hard rule — the exhaustive safety net must never be
    #    trimmed (a shot must never dead-end, and downstream consumers such as the
    #    promptlab excessive-duration linter read this whole chain to find a
    #    provider's clip ceiling). Compatibility skips apply to the STRATEGY
    #    selectors (cheapest/local/rules/tiers) and the explicit-pin guard, not
    #    here. An incompatible provider that only the safety net reaches is still
    #    stopped by generic_cloud's submit-time guard (the final defense).
    for pid in fallback_chain(shot):
        if pid not in order:
            order.append(pid)

    # local_only: NEVER cloud — strip any cloud provider from the whole order
    if else_sel == "local":
        kept: list[str] = []
        for pid in order:
            view = cat.get(pid)
            if view and view.kind == "cloud":
                skipped.append({"provider": pid,
                                "reason": "local_only strategy: cloud provider excluded"})
            else:
                kept.append(pid)
        order = kept

    return Resolution(shot.id, config.strategy, order, fired, else_sel, skipped,
                      fired_tier=fired_tier)


def resolved_order(project, shot) -> list[str]:
    """The ordered provider ids the generation path should try (routing on)."""
    return resolve(project, shot).order


def provider_disabled(pid: str) -> bool:
    """True when ``pid`` is a config-declared provider flagged ``disabled``."""
    from .registry import get_manifest

    manifest = get_manifest(pid)
    return bool(manifest and getattr(manifest, "disabled", False))


# ------------------------------------------------------------- CLI-facing views


def explain(project, shot) -> dict[str, Any]:
    """Which rule fired, which were skipped and why (§ debuggability)."""
    config = load_routing(project) or _default_config()
    res = resolve(project, shot, config)
    return {
        "shot": res.shot_id,
        "strategy": res.strategy,
        "sources": config.sources,
        "routing_file": bool(config.sources),
        "explicit_provider": shot.generation.provider,
        "tier": getattr(shot, "tier", None),
        "fired_rule": res.fired_rule,
        "fired_tier": res.fired_tier,
        "else": res.else_selector,
        "order": res.order,
        "chosen": res.chosen,
        "skipped": res.skipped,
    }


def validate_config(project) -> list[str]:
    """Full-file validation for `manju check` (round W, review #26): parse
    ``timeline/routing.yaml`` (+ the user default) and surface unknown
    strategy/tier/provider references and shape errors as 中文 problem
    strings. Empty list = clean (including "no routing file at all", which is
    always clean — routing.yaml is optional). Never raises: every
    :class:`RoutingError` is caught and turned into one line, so a bad routing
    file is a `manju check` FINDING, never a traceback."""
    problems: list[str] = []
    try:
        config = load_routing(project)
    except RoutingError as exc:
        return [f"timeline/routing.yaml: 解析失败 — {exc}"]
    if config is None:
        return problems

    cat = _catalog()

    def _check_provider(pid: str, where: str) -> None:
        view = cat.get(pid)
        if view is None or not view.exists:
            problems.append(f"routing: {where} 引用了未注册的 provider {pid!r}"
                            "(检查该 provider 的 manifest 是否存在)")

    names = list(BUILTIN_STRATEGIES) + [n for n in config.strategies if n not in BUILTIN_STRATEGIES]
    strat_defs: dict[str, dict[str, Any]] = {}
    for name in names:
        try:
            strat_defs[name] = _strategy_def(config, name)
        except RoutingError as exc:
            problems.append(f"routing: strategy {name!r} 定义有误 — {exc}")
    if config.strategy not in names:
        problems.append(
            f"routing: 激活策略 strategy: {config.strategy!r} 未定义 "
            f"— 可用策略: {sorted(names)}"
        )

    for name, sdef in strat_defs.items():
        for i, rule in enumerate(sdef["rules"]):
            _check_provider(rule["use"], f"strategy {name!r} rule[{i}].use")
        for pid in sdef["priority"]:
            _check_provider(pid, f"strategy {name!r} priority")
        if sdef["else"] not in META_ELSE:
            _check_provider(sdef["else"], f"strategy {name!r} else")

    for tname, traw in config.tiers.items():
        try:
            tdef = _normalize_tier(tname, traw)
        except RoutingError as exc:
            problems.append(f"routing: tier {tname!r} 定义有误 — {exc}")
            continue
        for pid in tdef["use"]:
            _check_provider(pid, f"tier {tname!r} use")

    return problems


def list_strategies(project) -> dict[str, Any]:
    config = load_routing(project) or _default_config()
    names = list(BUILTIN_STRATEGIES) + [n for n in config.strategies
                                        if n not in BUILTIN_STRATEGIES]
    out: list[dict[str, Any]] = []
    for name in names:
        strat = _strategy_def(config, name)  # raises RoutingError on a bad definition
        out.append({
            "name": name,
            "active": name == config.strategy,
            "builtin": name in BUILTIN_STRATEGIES,
            "rules": len(strat["rules"]),
            "else": strat["else"],
            "priority": strat["priority"],
        })
    tiers = [{"name": n, **_normalize_tier(n, config.tiers[n])}
             for n in sorted(config.tiers)]
    return {"active": config.strategy, "sources": config.sources,
            "strategies": out, "tiers": tiers}

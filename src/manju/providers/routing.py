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

Resolution order (the pin): ``shot.generation.provider`` (explicit always wins)
> the active strategy's first matching rule > the §8.4 fallback chain (always
the safety net). Disabled providers are skipped, the reason recorded. When
NEITHER routing file exists the whole module is bypassed by the registry, so
the provider choice is byte-identical to §8.4.
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


def _catalog() -> dict[str, _ProviderView]:
    """Every provider Manju knows about, disabled ones included, as a flat view
    the selectors read (kind for local_only, per_second for cheapest, disabled
    for the skip-reason ledger, capabilities for 'capable')."""
    from .manifest import (
        GENERIC_ADAPTER,
        GENERIC_ASR_ADAPTER,
        GENERIC_TTS_ADAPTER,
        load_manifests,
    )
    from .registry import available_providers

    cloud_adapters = {GENERIC_ADAPTER, GENERIC_TTS_ADAPTER, GENERIC_ASR_ADAPTER}
    cat: dict[str, _ProviderView] = {}
    manifests, _ = load_manifests()  # includes disabled + broken declarations
    for pid, m in manifests.items():
        cat[pid] = _ProviderView(
            id=pid,
            kind="cloud" if m.adapter in cloud_adapters else "local",
            capabilities=list(m.capabilities),
            per_second=float(m.cost.per_second),
            per_call=float(m.cost.per_call),
            disabled=bool(getattr(m, "disabled", False)),
            exists=True,
        )
    for pid, prov in available_providers().items():  # built-ins + constructed manifests
        if pid in cat:
            cat[pid].kind = prov.kind  # authoritative kind from the live instance
        else:
            cat[pid] = _ProviderView(pid, prov.kind, [], 0.0, 0.0, False, True)
    return cat


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

    @property
    def chosen(self) -> str | None:
        return self.order[0] if self.order else None


def _capable_sorted(cat: dict[str, _ProviderView], shot, *, kind: str | None = None,
                    by_cost: bool = False) -> list[str]:
    """Enabled providers advertising a capability this shot's pipeline calls
    for (its generation.fallback steps), optionally filtered to one kind and
    ordered per-second-cheapest first."""
    needs = set(shot.generation.fallback)
    cands = [v for v in cat.values()
             if v.exists and not v.disabled
             and (kind is None or v.kind == kind)
             and (set(v.capabilities) & needs)]
    cands.sort(key=(lambda v: (v.per_second, v.id)) if by_cost else (lambda v: v.id))
    return [v.id for v in cands]


def resolve(project, shot, config: RoutingConfig | None = None) -> Resolution:
    """Resolve the ordered provider list for one shot under the active strategy."""
    from .registry import fallback_chain

    if config is None:
        config = load_routing(project) or _default_config()
    cat = _catalog()
    strat = _strategy_def(config, config.strategy)
    else_sel = strat["else"]
    order: list[str] = []
    skipped: list[dict[str, Any]] = []

    def add(pid: str) -> None:
        view = cat.get(pid)
        if view is None or not view.exists:
            skipped.append({"provider": pid, "reason": "not registered / no manifest"})
            return
        if view.disabled:
            skipped.append({"provider": pid,
                            "reason": f"disabled (run: manju providers enable {pid})"})
            return
        if pid not in order:
            order.append(pid)

    # 1. explicit provider always wins (a disabled one is a hard build error
    #    raised in the registry before we get here; recorded as a skip in explain)
    if shot.generation.provider:
        add(shot.generation.provider)

    # 2. the active strategy's FIRST matching rule
    fired: dict[str, Any] | None = None
    for i, rule in enumerate(strat["rules"]):
        if _match(rule["match"], shot):
            fired = {"index": i, "match": rule["match"], "use": rule["use"]}
            add(rule["use"])
            break
        skipped.append({"rule": i, "reason": _why_no_match(rule["match"], shot)})

    # 3. the `else` selector — only when no rule fired
    if fired is None:
        if else_sel == "fallback":
            pass  # the safety net below is exactly this
        elif else_sel == "local":
            for pid in _capable_sorted(cat, shot, kind="local"):
                add(pid)
        elif else_sel == "cheapest":
            for pid in _capable_sorted(cat, shot, by_cost=True):
                add(pid)
        elif else_sel == "quality":
            for pid in strat["priority"]:
                add(pid)
        else:  # a literal provider id
            add(else_sel)

    # 4. §8.4 fallback chain — always the safety net (fallback_chain already
    #    drops disabled providers)
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

    return Resolution(shot.id, config.strategy, order, fired, else_sel, skipped)


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
        "fired_rule": res.fired_rule,
        "else": res.else_selector,
        "order": res.order,
        "chosen": res.chosen,
        "skipped": res.skipped,
    }


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
    return {"active": config.strategy, "sources": config.sources, "strategies": out}

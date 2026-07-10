"""DR05 — the MCP agent-surface ToolPolicy (schema ``manju.agent-tool-policy/v1``),
the single pure resolver, and the AgentSurfaceManifestV1 projection.

This module is PURE: no I/O, no network, no ``Project``, no time, no filesystem.
It is the ONE place that
  1. validates a tool's ``policy`` dict against the contract's enums + integrity
     rules (§7.3 / §7.8),
  2. resolves, for a profile, which tools are listed and which calls are
     admitted (:func:`resolve_agent_surface`) — the SINGLE source ``tools/list``,
     the call gate, and the ``agent_surface`` tool all read from, and
  3. projects the surface into the ``manju.agent-surface/v1`` manifest with a
     stable digest that excludes description text, handler identity, filesystem
     paths and time.

**Policy metadata is NOT a security boundary.** It describes a tool's real
effects so a profile can decline to LIST/CALL it; the actual enforcement lives
at call dispatch (this resolver, wired in :mod:`manju.mcp.tools`) plus the
existing engine guards (value-hash locks, CAS, the cross-process build lock, the
spend/ask_before gate, the director confirm gate). The manifest states
``raw_filesystem_enforced: false`` and ``project_can_override: false`` in every
profile precisely because MCP policy governs only THIS server's tool calls — it
does not sandbox the filesystem, and project content can never rewrite it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.hashing import hash_value

SCHEMA_POLICY = "manju.agent-tool-policy/v1"
SCHEMA_MANIFEST = "manju.agent-surface/v1"

# ---- effects (§7.3) — the semantic categories a tool's work falls into
READ = "READ"                    # reads project truth/state (may fill a rebuildable cache)
READ_RUNTIME = "READ_RUNTIME"    # reads server/runtime-derived state (the surface manifest)
WRITE_TRUTH = "WRITE_TRUTH"      # writes canonical shot truth (shots/*.yaml)
WRITE_PROPOSAL = "WRITE_PROPOSAL"  # writes an append-only proposal artifact (never edits truth)
WRITE_DERIVED = "WRITE_DERIVED"  # writes rebuildable derived output / evidence (reports, exports…)
NETWORK = "NETWORK"              # may make an outbound network call
SPEND = "SPEND"                  # may spend real money
EFFECTS = frozenset(
    {READ, READ_RUNTIME, WRITE_TRUTH, WRITE_PROPOSAL, WRITE_DERIVED, NETWORK, SPEND}
)
_WRITE_EFFECTS = frozenset({WRITE_TRUTH, WRITE_PROPOSAL, WRITE_DERIVED, NETWORK, SPEND})

# ---- network / spend levels
NEVER = "NEVER"
POSSIBLE = "POSSIBLE"
NETWORK_LEVELS = frozenset({NEVER, POSSIBLE})
SPEND_LEVELS = frozenset({NEVER, POSSIBLE})

# ---- gates (the engine guard(s) a tool's mutation actually passes through)
GATE_NONE = "NONE"
GATE_CAS = "CAS"                          # optimistic-concurrency precondition available
GATE_CHECKED_WRITE = "CHECKED_WRITE"      # lock-guard + post-write check + revert pipeline
GATE_BUILD_LOCK = "BUILD_LOCK"            # cross-process .manju/build.lock
GATE_SPEND_GATE = "SPEND_GATE"            # ask_before / budget breaker (§8.3)
GATE_CONFIRMED_PROPOSAL = "CONFIRMED_PROPOSAL"  # confirmed+current+human director gate
GATE_PROPOSAL_APPEND = "PROPOSAL_APPEND"  # atomic append-only proposal claim
GATES = frozenset(
    {GATE_NONE, GATE_CAS, GATE_CHECKED_WRITE, GATE_BUILD_LOCK, GATE_SPEND_GATE,
     GATE_CONFIRMED_PROPOSAL, GATE_PROPOSAL_APPEND}
)

# ---- concurrency posture
CONC_NONE = "NONE"                # read-only, no write serialization
CONC_BUILD_LOCK = "BUILD_LOCK"    # serialized by the cross-process build lock
CONC_ATOMIC_APPEND = "ATOMIC_APPEND"  # claim/append based, collision-safe, no build lock
CONCURRENCY = frozenset({CONC_NONE, CONC_BUILD_LOCK, CONC_ATOMIC_APPEND})

# ---- unattended rule (the per-tool decision under the unattended profile)
ALLOW = "ALLOW"
DENY = "DENY"
ALLOW_WITH_CAS = "ALLOW_WITH_CAS"
DRY_RUN_ONLY = "DRY_RUN_ONLY"
CONFIRMED_PROPOSAL_ONLY = "CONFIRMED_PROPOSAL_ONLY"
UNATTENDED_RULES = frozenset(
    {ALLOW, DENY, ALLOW_WITH_CAS, DRY_RUN_ONLY, CONFIRMED_PROPOSAL_ONLY}
)

# ---- profiles (EXACTLY two)
COLLABORATIVE = "collaborative"
UNATTENDED = "unattended"
PROFILES = frozenset({COLLABORATIVE, UNATTENDED})

DENIAL_CODE = "agent_profile_denied"

_POLICY_KEYS = ("effects", "network", "spend", "gate", "concurrency", "unattended")


class PolicyError(ValueError):
    """A tool policy violates the schema or an integrity rule (§7.8)."""


def policy(
    *,
    effects: list[str],
    network: str = NEVER,
    spend: str = NEVER,
    gate: tuple[str, ...] | list[str] = (GATE_NONE,),
    concurrency: str = CONC_NONE,
    unattended: str = ALLOW,
    writes: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Build a policy dict (a pure value — lists of strings only). Kept a builder
    so each TOOL_DEFS entry stays a single inline policy; there is no second
    registry."""
    return {
        "effects": list(effects),
        "network": network,
        "spend": spend,
        "gate": list(gate),
        "concurrency": concurrency,
        "unattended": unattended,
        "writes": list(writes),
    }


# ------------------------------------------------------------- validation (§7.8)


def _is_pure_leaf(v: Any) -> bool:
    # a policy value is either a string, a bool, or a list of strings — never a
    # callable, Path, credential, number (time), or nested dict.
    if isinstance(v, Path) or callable(v):
        return False
    if isinstance(v, bool):
        return True
    if isinstance(v, str):
        return True
    if isinstance(v, list):
        return all(isinstance(x, str) for x in v)
    return False


def validate_policy(name: str, pol: Any) -> None:
    """Validate one tool's policy against the enums + the §7.8 integrity rules.
    Raises :class:`PolicyError` on any violation."""
    if not isinstance(pol, dict):
        raise PolicyError(f"{name}: policy must be a dict")
    missing = [k for k in _POLICY_KEYS if k not in pol]
    if missing:
        raise PolicyError(f"{name}: policy missing keys {missing}")

    # purity: no callables / Paths / credentials / time in any value (§7.8)
    for key, val in pol.items():
        if not _is_pure_leaf(val):
            raise PolicyError(f"{name}.{key}: impure policy value {val!r}")

    effects = pol["effects"]
    if not isinstance(effects, list):
        raise PolicyError(f"{name}.effects must be a list")
    illegal = set(effects) - EFFECTS
    if illegal:
        raise PolicyError(f"{name}.effects illegal tokens {sorted(illegal)}")
    if pol["network"] not in NETWORK_LEVELS:
        raise PolicyError(f"{name}.network illegal {pol['network']!r}")
    if pol["spend"] not in SPEND_LEVELS:
        raise PolicyError(f"{name}.spend illegal {pol['spend']!r}")
    gate = pol["gate"]
    if not isinstance(gate, list) or not gate:
        raise PolicyError(f"{name}.gate must be a non-empty list")
    illegal_g = set(gate) - GATES
    if illegal_g:
        raise PolicyError(f"{name}.gate illegal tokens {sorted(illegal_g)}")
    if pol["concurrency"] not in CONCURRENCY:
        raise PolicyError(f"{name}.concurrency illegal {pol['concurrency']!r}")
    if pol["unattended"] not in UNATTENDED_RULES:
        raise PolicyError(f"{name}.unattended illegal {pol['unattended']!r}")

    writes = pol.get("writes", [])
    if not isinstance(writes, list):
        raise PolicyError(f"{name}.writes must be a list")
    for w in writes:
        if not isinstance(w, str) or not w:
            raise PolicyError(f"{name}.writes entry must be a non-empty string: {w!r}")
        if w.startswith("/") or w.startswith("~"):
            raise PolicyError(f"{name}.writes must be project-relative, not {w!r}")
        if ".." in w.split("/"):
            raise PolicyError(f"{name}.writes must not traverse: {w!r}")

    # ---- cross-field integrity rules (§7.8)
    if (SPEND in effects) != (pol["spend"] != NEVER):
        raise PolicyError(f"{name}: SPEND effect must iff spend != NEVER")
    if (NETWORK in effects) != (pol["network"] != NEVER):
        raise PolicyError(f"{name}: NETWORK effect must iff network != NEVER")
    if WRITE_TRUTH in effects and (not gate or GATE_NONE in gate):
        raise PolicyError(f"{name}: WRITE_TRUTH must not have gate=NONE")
    if pol["unattended"] == ALLOW_WITH_CAS and GATE_CAS not in gate:
        raise PolicyError(f"{name}: ALLOW_WITH_CAS must declare a CAS gate")
    if (pol["unattended"] == CONFIRMED_PROPOSAL_ONLY) != (GATE_CONFIRMED_PROPOSAL in gate):
        raise PolicyError(f"{name}: CONFIRMED_PROPOSAL_ONLY iff gate has CONFIRMED_PROPOSAL")
    if pol["unattended"] == DRY_RUN_ONLY and not ({SPEND, NETWORK} & set(effects)):
        raise PolicyError(f"{name}: DRY_RUN_ONLY only for a spend/network tool")
    # a pure-read tool (no write/network/spend effect) must never spend or dial out
    if not (set(effects) & _WRITE_EFFECTS):
        if pol["network"] != NEVER or pol["spend"] != NEVER:
            raise PolicyError(f"{name}: a read-only tool must be network/spend NEVER")


def validate_registry(tool_defs: list[dict[str, Any]]) -> None:
    """Load/test-time integrity gate: every tool has a valid policy. Raises
    :class:`PolicyError` on the first offender."""
    for t in tool_defs:
        name = t.get("name", "<unnamed>")
        if "policy" not in t:
            raise PolicyError(f"{name}: no policy dict (every tool must declare one)")
        validate_policy(name, t["policy"])


# --------------------------------------------------------------- the resolver


def _required_path(name: str, rule: str) -> str | None:
    if rule == ALLOW_WITH_CAS:
        return (
            "call get_shot first and pass its `rev` back as `expected_rev` — under "
            "the unattended profile a shot write must be CAS-guarded so it cannot "
            "clobber an edit made since you loaded it"
        )
    if rule == DRY_RUN_ONLY:
        return (
            'call build with {"dry_run": true} for a cost estimate; real generation '
            "must go through a human-confirmed director proposal (director_propose → "
            "a human director_confirm → director_execute)"
        )
    if rule == DENY:
        if name == "director_confirm":
            return (
                "an unattended agent may not confirm — a human confirms via `manju "
                "director confirm` or the GUI director page (approve-before-execute, §8.3)"
            )
        if name == "redo":
            return (
                "propose the redo (director_propose) and let a human confirm + execute "
                "it; an unattended agent never forces paid takes"
            )
        return "route this tool through a human; it is not available to an unattended agent"
    return None


class CallDecision:
    """The admission verdict for one (tool, arguments) under a profile."""

    __slots__ = ("tool", "profile", "admitted", "reason", "required_path")

    def __init__(
        self,
        tool: str,
        profile: str,
        admitted: bool,
        *,
        reason: str | None = None,
        required_path: str | None = None,
    ):
        self.tool = tool
        self.profile = profile
        self.admitted = admitted
        self.reason = reason
        self.required_path = required_path

    def denial_payload(self) -> dict[str, Any]:
        """The structured ``agent_profile_denied`` payload (§ ruling 4) — never a
        natural-language-only error, never disguised as unknown-tool."""
        return {
            "error": self.reason or f"{self.tool} is not available under the {self.profile} profile",
            "code": DENIAL_CODE,
            "tool": self.tool,
            "profile": self.profile,
            "required_path": self.required_path,
        }


class SurfaceEntry:
    __slots__ = ("name", "policy", "listed")

    def __init__(self, name: str, pol: dict[str, Any], listed: bool):
        self.name = name
        self.policy = pol
        self.listed = listed

    @property
    def unattended(self) -> str:
        return self.policy["unattended"]


class Surface:
    """The resolved agent surface for ONE profile. tools/list reads
    :meth:`listed_names`; the call gate reads :meth:`decide`; the agent_surface
    tool reads :meth:`manifest` / :meth:`digest`. One object, one source."""

    def __init__(self, profile: str, entries: dict[str, SurfaceEntry], order: list[str],
                 call_arguments: dict | None = None):
        self.profile = profile
        self.entries = entries
        self._order = order
        self._pending = call_arguments

    def listed_names(self) -> list[str]:
        return [n for n in self._order if self.entries[n].listed]

    def decide(self, name: str, arguments: dict | None = None) -> CallDecision:
        entry = self.entries.get(name)
        if entry is None:
            raise KeyError(name)  # unknown-tool is the caller's concern, not policy's
        args = arguments if arguments is not None else (self._pending or {})
        # collaborative is byte/semantics-identical to today: every tool admitted.
        if self.profile == COLLABORATIVE:
            return CallDecision(name, self.profile, True)
        rule = entry.unattended
        if rule == ALLOW:
            return CallDecision(name, self.profile, True)
        if rule == CONFIRMED_PROPOSAL_ONLY:
            # the profile admits the CALL; the engine gate re-checks confirmed/
            # current/human — the profile never replaces it.
            return CallDecision(name, self.profile, True)
        if rule == ALLOW_WITH_CAS:
            rev = args.get("expected_rev")
            if isinstance(rev, str) and rev:
                return CallDecision(name, self.profile, True)
            return self._deny(name, rule)
        if rule == DRY_RUN_ONLY:
            if args.get("dry_run") is True:  # dry_run short-circuits before any spend
                return CallDecision(name, self.profile, True)
            return self._deny(name, rule)
        # DENY
        return self._deny(name, rule)

    def _deny(self, name: str, rule: str) -> CallDecision:
        rp = _required_path(name, rule)
        return CallDecision(
            name, self.profile, False,
            reason=f"{name} is not available under the {self.profile} agent profile: {rp}",
            required_path=rp,
        )

    # ---- projection

    def _tool_projection(self) -> list[dict[str, Any]]:
        out = []
        for name in self._order:
            e = self.entries[name]
            p = e.policy
            out.append({
                "name": name,
                "effects": list(p["effects"]),
                "network": p["network"],
                "spend": p["spend"],
                "gate": list(p["gate"]),
                "concurrency": p["concurrency"],
                "unattended": p["unattended"],
                "listed": e.listed,
                "writes": list(p.get("writes", [])),
            })
        return out

    def _digest_payload(self) -> dict[str, Any]:
        # digest EXCLUDES description text, handler identity, writes[] paths and
        # time — only the stable semantic policy fields + the profile projection.
        items = []
        for name in sorted(self.entries):
            e = self.entries[name]
            p = e.policy
            items.append([
                name, sorted(p["effects"]), p["network"], p["spend"],
                sorted(p["gate"]), p["concurrency"], p["unattended"], e.listed,
            ])
        return {
            "schema": SCHEMA_MANIFEST,
            "profile": self.profile,
            "raw_filesystem_enforced": False,
            "project_can_override": False,
            "tools": items,
        }

    def digest(self) -> str:
        return hash_value(self._digest_payload())

    def manifest(self) -> dict[str, Any]:
        """The AgentSurfaceManifestV1 for this profile — derived + rebuildable,
        never persisted."""
        return {
            "schema": SCHEMA_MANIFEST,
            "profile": self.profile,
            # honesty (§ ruling 6): MCP policy governs only THIS server's tool
            # calls — it does not sandbox the filesystem, and project content
            # cannot rewrite it.
            "raw_filesystem_enforced": False,
            "project_can_override": False,
            "digest": self.digest(),
            "tools": self._tool_projection(),
        }


def _listed(profile: str, pol: dict[str, Any]) -> bool:
    if profile == COLLABORATIVE:
        return True
    # unattended: a DENY tool is hidden from tools/list; everything else is
    # listed (an argument-conditional tool is still discoverable + usable).
    return pol["unattended"] != DENY


def resolve_agent_surface(
    tool_defs: list[dict[str, Any]],
    profile: str,
    call_arguments: dict | None = None,
) -> Surface:
    """THE single pure resolver. Builds the :class:`Surface` for ``profile`` from
    the ONE registry. ``call_arguments`` (optional) pre-binds a pending call's
    arguments so :meth:`Surface.decide` can be argument-aware without re-passing
    them; ``tools/list`` and the manifest ignore it. Raises :class:`ValueError`
    for an unknown profile — the profile can come ONLY from the server flag, so a
    bad value is a programming/config error, never project content."""
    if profile not in PROFILES:
        raise ValueError(
            f"unknown agent profile {profile!r} (expected one of {sorted(PROFILES)})"
        )
    entries: dict[str, SurfaceEntry] = {}
    order: list[str] = []
    for t in tool_defs:
        name = t["name"]
        pol = t["policy"]
        entries[name] = SurfaceEntry(name, pol, _listed(profile, pol))
        order.append(name)
    return Surface(profile, entries, order, call_arguments)

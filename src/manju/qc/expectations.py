"""ExpectationSet compilation (DR02 WP1) — the derived, LLM-free contract of
what a shot's AUTHOR explicitly promised the picture would show.

An ExpectationSet is a DERIVED artifact: it is compiled read-only from a shot's
EXPLICIT authored constraints and never writes back to any source. It compiles
from ONLY three sources (DR02 non-negotiable + orchestrator ruling #3):

    quality.must_show[i]   -> polarity=present, check=external_visual
    quality.avoid[i]       -> polarity=absent,  check=external_visual
    continuity.locks[i]    -> polarity=present, check=external_consistency

Nothing else. Action/dialogue/story free text, prompts, reviewer summaries, and
model confidence are FORBIDDEN sources — a promise is something the author wrote
down as a constraint, not something a model inferred. Deterministic technical
checks (resolution, duration, OCR must_show machine tier) stay in ``run_qc`` and
gate acceptance through the separate "no policy-blocking error" condition; they
are deliberately NOT duplicated here as expectations (ruling #3).

Identity vs provenance. An expectation's ``id`` is content-derived from its
polarity + a normalized form of its statement, so it is STABLE under list
reorder and independent of YAML key order; ``source_path`` records the authored
index purely as provenance. The set ``digest`` is a canonical hash over the
expectation IDENTITIES (sorted by id, provenance excluded) so the same
semantics under a different key/list order yield an identical digest, while any
authored text change moves it. An empty set is a valid state meaning "nothing
was promised" — never auto-invent a requirement.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import TYPE_CHECKING

from ..core.hashing import hash_value
from ..core.intent import expectation_assertions
from ..core.spec import SPEC_VERSION, compute_spec_hash

if TYPE_CHECKING:
    from ..core.container import Project

SCHEMA = "manju.qc.expectations/v1"
SCHEMA_V2 = "manju.qc.expectations/v2"

# kind -> (polarity, check). The kind is the id's human-readable middle segment
# (``exp:<shot>:<kind>:<sha8>``) and the dedup bucket; the source field path is
# recorded separately as ``source_path``.
_KINDS = {
    "must_show": ("present", "external_visual"),
    "avoid": ("absent", "external_visual"),
    "lock": ("present", "external_consistency"),
}

_WS_RE = re.compile(r"\s+")


def _normalize(statement: str) -> str:
    """Normalize a statement for IDENTITY only (NFC unicode + strip + collapse
    internal whitespace). The stored ``statement`` stays VERBATIM (ruling #6:
    must_show/avoid/lock strings are stored verbatim repo-wide) — this
    normalization applies ONLY inside the expectation-id / digest identity, so
    two authors who differ only by unicode form or incidental whitespace land on
    the same id."""
    s = unicodedata.normalize("NFC", statement)
    s = s.strip()
    return _WS_RE.sub(" ", s)


def _expectation_id(shot_id: str, kind: str, polarity: str, statement: str) -> str:
    norm = _normalize(statement)
    digest = hashlib.sha256((polarity + "\n" + norm).encode("utf-8")).hexdigest()
    return f"exp:{shot_id}:{kind}:{digest[:8]}"


def compile_expectations(project: "Project", shot_id: str) -> dict:
    """Compile the ExpectationSet for one shot (schema ``manju.qc.expectations/
    v1``). Read-only: loads the shot + bible, never writes. A shot with no
    explicit must_show / avoid / continuity.locks yields ``expectations: []``
    (a valid "nothing was promised" state), and an old shot whose file predates
    the quality/continuity sections loads fine (the model defaults them empty).

    ``spec_hash`` is the shot's CURRENT spec hash via the existing machinery
    (``compute_spec_hash(..., version=SPEC_VERSION, project_root=...)`` — the
    same call build/stale.py makes for the current hash); it is never
    re-derived here.
    """
    shot = project.load_shot(shot_id)
    bible = project.load_bible()
    spec_hash = compute_spec_hash(
        shot, bible, version=SPEC_VERSION, project_root=project.root
    )

    if shot.contract is not None:
        return _compile_v2(shot, spec_hash)

    # (kind, statement, source_path) in authored order, all three sources.
    authored: list[tuple[str, str, str]] = []
    for i, s in enumerate(shot.quality.must_show):
        authored.append(("must_show", s, f"shots/{shot_id}.yaml#/quality/must_show/{i}"))
    for i, s in enumerate(shot.quality.avoid):
        authored.append(("avoid", s, f"shots/{shot_id}.yaml#/quality/avoid/{i}"))
    for i, s in enumerate(shot.continuity.locks):
        authored.append(("lock", s, f"shots/{shot_id}.yaml#/continuity/locks/{i}"))

    by_id: dict[str, dict] = {}
    for kind, statement, source_path in authored:
        polarity, check = _KINDS[kind]
        eid = _expectation_id(shot_id, kind, polarity, statement)
        if eid in by_id:
            # exact duplicate (same kind+polarity+normalized statement) — collapse
            # to the first occurrence, record the extra provenance as a diagnostic.
            by_id[eid].setdefault("duplicates", []).append(source_path)
            continue
        by_id[eid] = {
            "id": eid,
            "source_path": source_path,
            "polarity": polarity,
            "check": check,
            "statement": statement,  # verbatim
            "required": True,
        }

    expectations = sorted(by_id.values(), key=lambda e: e["id"])
    digest = _digest(shot_id, expectations)

    return {
        "schema": SCHEMA,
        "subject": {"kind": "shot", "id": shot_id},
        "spec_hash": spec_hash,
        "expectations": expectations,
        "digest": digest,
    }


def _expectation_id_v2(
    shot_id: str, kind: str, polarity: str, position: str, statement: str
) -> str:
    identity = "\n".join((shot_id, kind, polarity, position, _normalize(statement)))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"exp:{shot_id}:{kind}:{digest[:8]}"


def _source_pointer(shot_id: str, source_path: str) -> str:
    pointer = re.sub(r"\[([0-9]+)\]", r"/\1", source_path.replace(".", "/"))
    return f"shots/{shot_id}.yaml#/{pointer}"


def _compile_v2(shot, spec_hash: str) -> dict:
    by_id: dict[str, dict] = {}
    for assertion in expectation_assertions(shot):
        eid = _expectation_id_v2(
            shot.id,
            assertion.kind,
            assertion.polarity,
            assertion.position,
            assertion.statement,
        )
        source_path = _source_pointer(shot.id, assertion.source_path)
        if eid in by_id:
            by_id[eid].setdefault("duplicates", []).append(source_path)
            continue
        by_id[eid] = {
            "id": eid,
            "source_path": source_path,
            "polarity": assertion.polarity,
            "check": assertion.check,
            "statement": assertion.statement,
            "position": assertion.position,
            "required": True,
        }

    expectations = sorted(by_id.values(), key=lambda item: item["id"])
    return {
        "schema": SCHEMA_V2,
        "subject": {"kind": "shot", "id": shot.id},
        "spec_hash": spec_hash,
        "expectations": expectations,
        "digest": _digest_v2(shot.id, expectations),
    }


def _digest_v2(shot_id: str, expectations: list[dict]) -> str:
    identity = [
        {
            "id": item["id"],
            "polarity": item["polarity"],
            "check": item["check"],
            "position": item["position"],
            "statement": _normalize(item["statement"]),
            "required": item["required"],
        }
        for item in sorted(expectations, key=lambda item: item["id"])
    ]
    return hash_value(
        {
            "schema": SCHEMA_V2,
            "subject": {"kind": "shot", "id": shot_id},
            "expectations": identity,
        }
    )


def _digest(shot_id: str, expectations: list[dict]) -> str:
    """Canonical hash over the expectation IDENTITIES only — sorted by id, with
    provenance (``source_path``/``duplicates``) and the shot's spec_hash
    EXCLUDED. Excluding provenance is what makes the digest reorder-stable (the
    contract: 'digest reflects set not order'); excluding spec_hash keeps the
    binding independent (a locks edit moves the digest but not spec_hash, a
    camera edit moves spec_hash but not the digest). The statement is folded in
    NORMALIZED so the digest matches the id's identity notion."""
    identity = [
        {
            "id": e["id"],
            "polarity": e["polarity"],
            "check": e["check"],
            "statement": _normalize(e["statement"]),
            "required": e["required"],
        }
        for e in sorted(expectations, key=lambda e: e["id"])
    ]
    return hash_value({
        "schema": SCHEMA,
        "subject": {"kind": "shot", "id": shot_id},
        "expectations": identity,
    })


def expectation_ids(expectation_set: dict) -> set[str]:
    """The set of expectation ids in a compiled set — the membership oracle for
    verdict-intake step 7 (every observation must reference a known id)."""
    return {e["id"] for e in expectation_set.get("expectations", [])}

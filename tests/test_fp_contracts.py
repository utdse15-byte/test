"""F0 Contract Governance — registry ⇄ code consistency (roadmap §4.1/§4.2/§4.7).

These tests are the enforcement half of the declared registry in CONTRACTS.yaml.
They fail RED the moment the registry and the code drift apart:

  (a) a `manju.*/vN` schema literal ships in src but is not registered;
  (b) a registered schema id no longer appears anywhere in src (a ghost row);
  (c) a status is outside the four allowed levels, or a stable schema forgets
      to promise read_older;
  (d) the §4.7 ownership map loses one of its authoritative concepts.

Nothing here imports the registry into the engine — governance stays declared
+ test-enforced, never a runtime input.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from manju.core import contracts

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "manju"

# The audit's grep, quote-agnostic: a schema id as a string literal in src.
_SCHEMA_LITERAL = re.compile(r"""["']manju\.[a-z0-9._-]+/v[0-9]+["']""")


def _src_schema_literals() -> set[str]:
    """Every `manju.*/vN` string literal across src/manju/**/*.py."""
    found: set[str] = set()
    for py in SRC_ROOT.rglob("*.py"):
        for m in _SCHEMA_LITERAL.finditer(py.read_text(encoding="utf-8")):
            found.add(m.group(0).strip("\"'"))
    return found


# ---------------------------------------------------------------- (a) + (b)


def test_every_src_schema_literal_is_registered():
    """(a) Every schema id emitted by src is in the registry."""
    registered = set(contracts.schema_ids())
    unregistered = sorted(_src_schema_literals() - registered)
    assert not unregistered, (
        "these `manju.*/vN` schema ids ship in src but are NOT in CONTRACTS.yaml — "
        f"register them (schemas:) before shipping: {unregistered}"
    )


def test_no_ghost_schema_entries():
    """(b) Every registered schema id still appears in src (no ghost rows)."""
    literals = _src_schema_literals()
    ghosts = sorted(sid for sid in contracts.schema_ids() if sid not in literals)
    assert not ghosts, (
        "these schema ids are registered in CONTRACTS.yaml but appear nowhere in "
        f"src/manju — remove the ghost rows or restore the code: {ghosts}"
    )


def test_registry_schema_ids_match_src_exactly():
    """The registered schema set equals the src literal set (both directions)."""
    assert set(contracts.schema_ids()) == _src_schema_literals()


def test_schema_id_count_meets_the_audited_floor():
    """40 distinct schema ids were the audited inventory at registry creation
    (2026-07-12). This is a FLOOR, not equality: the bidirectional literal⇄
    registry exact-match tests above already prevent drift in both directions, so
    the count's only remaining job is to catch a TRUNCATED registry. A floor does
    that and survives legitimate growth — a later loop registering new schemas
    (e.g. media-technical-profile / conform-loss) is the intended path, not a
    regression."""
    assert len(contracts.schema_ids()) >= 40


# -------------------------------------------------------------------- (c)


def test_all_statuses_are_valid_levels():
    """(c) Every entry's status is one of the four §4.2 levels."""
    for cid, e in contracts.registry().items():
        assert e["status"] in contracts.VALID_STATUSES, (
            f"{cid}: status {e['status']!r} is not one of {contracts.VALID_STATUSES}")


def test_stable_entries_declare_read_older():
    """(c) A stable contract promises to read its older majors (§4.2/§4.4)."""
    offenders = sorted(
        cid for cid, e in contracts.registry().items()
        if e["status"] == "stable" and e["read_older"] is not True
    )
    assert not offenders, (
        "stable contracts must declare read_older: true (additive-only / read the "
        f"past) — these do not: {offenders}"
    )


def test_latest_version_matches_schema_id_major():
    """A schema row's latest_version equals the integer major in its id."""
    for sid in contracts.schema_ids():
        major = int(sid.rsplit("/v", 1)[1])
        assert contracts.entry(sid)["latest_version"] == major, (
            f"{sid}: latest_version must equal the major in the id ({major})")


def test_colorstats_projections_stay_experimental():
    """The two colorstats projections MUST stay experimental — they must never be
    silently promoted to stable without review. This is a membership + direction
    check, deliberately NOT set-equality: new experimental schemas are the
    intended arrival state for every future contract (stable is a promotion, not
    a default for newcomers), so registering a new experimental id must not fail
    here. The deliberateness of each experimental row is enforced by it being a
    reviewed commit change plus the literal⇄registry consistency tests above."""
    for sid in ("manju.qc.colorstats.preview/v1", "manju.qc.colorstats.compare/v1"):
        assert contracts.entry(sid)["status"] == "experimental", (
            f"{sid} must stay experimental (never silently promoted to stable)")


def test_write_older_is_false_everywhere():
    """Downgrade is never assumed (§4.4): no contract emits a prior major today."""
    for cid, e in contracts.registry().items():
        assert e["write_older"] is False, (
            f"{cid}: write_older is true but no downgrade path ships this loop")


# -------------------------------------------------------------------- (d)


# The §4.7 object ownership map — one authoritative owner per concept. Encoded
# here as the authoritative expectation; CONTRACTS.yaml must cover exactly these.
OWNERSHIP_CONCEPTS = frozenset({
    "shot_intent",
    "character_identity",
    "selected_take",
    "provider_request",
    "quality_evidence",
    "release_approval",
    "delivery_list",
    "runtime_state",
})


def test_ownership_table_is_complete():
    """(d) The registry's ownership map covers every §4.7 concept, exactly."""
    declared = set(contracts.ownership())
    assert declared == OWNERSHIP_CONCEPTS, (
        f"ownership map drift — missing {sorted(OWNERSHIP_CONCEPTS - declared)}, "
        f"unexpected {sorted(declared - OWNERSHIP_CONCEPTS)}")


def test_ownership_owners_are_non_empty():
    for concept, owner in contracts.ownership().items():
        assert isinstance(owner, str) and owner.strip(), (
            f"{concept}: needs a single authoritative owner string")


# -------------------------------------------------- owner modules are real code


def test_every_owner_resolves_to_real_code():
    """Each registered owner ('pkg.mod' or 'pkg.mod:Symbol') imports — the
    registry can never name an owner module/symbol that does not exist."""
    for cid, e in contracts.registry().items():
        owner = e["owner"]
        mod_path, _, symbol = owner.partition(":")
        try:
            mod = importlib.import_module(mod_path)
        except ImportError as exc:  # pragma: no cover - failure is the signal
            pytest.fail(f"{cid}: owner module {mod_path!r} does not import ({exc})")
        if symbol:
            assert hasattr(mod, symbol), (
                f"{cid}: owner {owner!r} names {symbol!r} which {mod_path} lacks")


# ------------------------------------------------------- planned migration pin


def test_declared_future_major_is_recorded_not_implemented():
    """The declared fps int→rational edit_rate major is on record AND still
    unimplemented — models.ProjectConfig.fps is int today."""
    migs = contracts.planned_migrations()
    assert migs, "planned_migrations must record the declared future major"
    first = migs[0]
    assert first["id"] == "project.fps-int-to-rational-edit-rate"
    assert first["implemented"] is False

    # the pin's factual anchor: fps is genuinely an int in the model today.
    from manju.core.models import ProjectConfig
    assert ProjectConfig.model_fields["fps"].annotation is int


# --------------------------------------------------------- loader shape guards


def test_loader_rejects_unknown_status(tmp_path):
    """The loader errors on an out-of-vocabulary status (shape validation)."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schemas:\n"
        "  - id: manju.x/v1\n"
        "    kind: schema\n"
        "    owner: manju.core.models\n"
        "    status: totally-made-up\n"
        "    latest_version: 1\n"
        "    read_older: true\n"
        "    write_older: false\n"
        "    notes: x\n",
        encoding="utf-8",
    )
    with pytest.raises(contracts.ContractsError):
        contracts._load(str(bad))


def test_loader_rejects_missing_fields(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schemas:\n"
        "  - id: manju.x/v1\n"
        "    kind: schema\n"
        "    status: stable\n",
        encoding="utf-8",
    )
    with pytest.raises(contracts.ContractsError):
        contracts._load(str(bad))


def test_loader_rejects_duplicate_ids(tmp_path):
    bad = tmp_path / "bad.yaml"
    row = (
        "  - id: manju.x/v1\n"
        "    kind: schema\n"
        "    owner: manju.core.models\n"
        "    status: stable\n"
        "    latest_version: 1\n"
        "    read_older: true\n"
        "    write_older: false\n"
        "    notes: x\n"
    )
    bad.write_text("schemas:\n" + row + row, encoding="utf-8")
    with pytest.raises(contracts.ContractsError):
        contracts._load(str(bad))


def test_entry_unknown_id_raises_keyerror():
    with pytest.raises(KeyError):
        contracts.entry("manju.does-not-exist/v1")

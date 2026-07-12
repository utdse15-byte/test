"""Contract registry loader (F0 Contract Governance, roadmap §4.1/§4.2/§4.7).

A small, pure reader over ``CONTRACTS.yaml`` at the repository root — the single
declared registry of every public contract Manju ships.

This module is DECLARED + TEST-ENFORCED governance and NOTHING more. It is never
an execution input: no build/qc/provider/CLI path imports it, and the registry
is consumed only by the F0 tests (tests/test_fp_contracts.py). Governance is a
label discipline, not runtime plumbing — wiring the registry into the engine
would make a documentation file a build input, exactly the second-source
mistake §4 warns against. The only I/O here is reading the one YAML file.

Public API:
    registry()            -> dict[id, entry]   (schemas + documents, by id)
    entry(contract_id)    -> entry dict         (raises KeyError if unknown)
    schema_ids()          -> frozenset[str]     (the `manju.*/vN` schema rows)
    ownership()           -> dict[concept, owner]
    planned_migrations()  -> list[dict]
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from .yamlio import read_yaml

# repo root holds CONTRACTS.yaml next to DECISIONS.md; this file is
# src/manju/core/contracts.py -> parents[3] is the repo root.
REGISTRY_PATH = Path(__file__).resolve().parents[3] / "CONTRACTS.yaml"

# §4.2 — the four (and only four) contract status levels.
VALID_STATUSES = ("stable", "experimental", "internal", "deprecated")
VALID_KINDS = ("schema", "document")

# every registry entry carries exactly these keys (all required).
REQUIRED_FIELDS = (
    "id", "kind", "owner", "status",
    "latest_version", "read_older", "write_older", "notes",
)


class ContractsError(ValueError):
    """CONTRACTS.yaml is missing, malformed, or shape-invalid."""


def _validate_entry(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ContractsError(f"{where}: entry must be a mapping, got {type(raw).__name__}")
    missing = [f for f in REQUIRED_FIELDS if f not in raw]
    if missing:
        raise ContractsError(f"{where}: entry {raw.get('id')!r} missing fields {missing}")
    cid = raw["id"]
    if not (isinstance(cid, str) and cid.strip()):
        raise ContractsError(f"{where}: entry has an empty/non-string id")
    if raw["kind"] not in VALID_KINDS:
        raise ContractsError(
            f"{where}: {cid!r} has unknown kind {raw['kind']!r} "
            f"(must be one of {VALID_KINDS})")
    if raw["status"] not in VALID_STATUSES:
        raise ContractsError(
            f"{where}: {cid!r} has unknown status {raw['status']!r} "
            f"(must be one of {VALID_STATUSES})")
    if not isinstance(raw["latest_version"], int) or isinstance(raw["latest_version"], bool):
        raise ContractsError(f"{where}: {cid!r} latest_version must be an int")
    for flag in ("read_older", "write_older"):
        if not isinstance(raw[flag], bool):
            raise ContractsError(f"{where}: {cid!r} {flag} must be a bool")
    if not isinstance(raw["owner"], str) or not raw["owner"].strip():
        raise ContractsError(f"{where}: {cid!r} owner must be a non-empty module path")
    return dict(raw)


@lru_cache(maxsize=None)
def _load(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    if not path.exists():
        raise ContractsError(f"contract registry not found: {path}")
    doc = read_yaml(path)
    if not isinstance(doc, dict):
        raise ContractsError(f"{path}: top level must be a mapping")

    schemas = doc.get("schemas") or []
    documents = doc.get("documents") or []
    if not isinstance(schemas, list) or not isinstance(documents, list):
        raise ContractsError(f"{path}: 'schemas' and 'documents' must be lists")

    entries: dict[str, dict[str, Any]] = {}
    schema_id_set: set[str] = set()
    for raw in schemas:
        e = _validate_entry(raw, "schemas")
        if e["kind"] != "schema":
            raise ContractsError(f"schemas: {e['id']!r} must have kind 'schema'")
        if e["id"] in entries:
            raise ContractsError(f"duplicate contract id: {e['id']!r}")
        entries[e["id"]] = e
        schema_id_set.add(e["id"])
    for raw in documents:
        e = _validate_entry(raw, "documents")
        if e["kind"] != "document":
            raise ContractsError(f"documents: {e['id']!r} must have kind 'document'")
        if e["id"] in entries:
            raise ContractsError(f"duplicate contract id: {e['id']!r}")
        entries[e["id"]] = e

    ownership_raw = doc.get("ownership") or {}
    if not isinstance(ownership_raw, dict):
        raise ContractsError(f"{path}: 'ownership' must be a mapping")

    migrations = doc.get("planned_migrations") or []
    if not isinstance(migrations, list):
        raise ContractsError(f"{path}: 'planned_migrations' must be a list")

    return {
        "entries": entries,
        "schema_ids": frozenset(schema_id_set),
        "ownership": dict(ownership_raw),
        "planned_migrations": list(migrations),
    }


def _data() -> dict[str, Any]:
    return _load(str(REGISTRY_PATH))


def registry() -> dict[str, dict[str, Any]]:
    """Every registered contract (schemas + documents) keyed by id."""
    return dict(_data()["entries"])


def entry(contract_id: str) -> dict[str, Any]:
    """The registry entry for ``contract_id``. Raises ``KeyError`` if unknown."""
    entries = _data()["entries"]
    if contract_id not in entries:
        raise KeyError(f"no contract registered for id {contract_id!r}")
    return dict(entries[contract_id])


def schema_ids() -> frozenset[str]:
    """The ids of the `manju.*/vN` schema rows (kind == 'schema')."""
    return _data()["schema_ids"]


def ownership() -> dict[str, str]:
    """The §4.7 concept -> authoritative-owner map."""
    return dict(_data()["ownership"])


def planned_migrations() -> list[dict[str, Any]]:
    """Declared (not necessarily implemented) future contract migrations."""
    return [dict(m) for m in _data()["planned_migrations"]]

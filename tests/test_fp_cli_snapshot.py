"""F0 Contract Governance — CLI compatibility snapshot (roadmap §4.6).

The stable CLI surface is FROZEN in tests/fixtures/cli_surface.json: for every
command path, the required parameters it must keep. The snapshot is generated
from the typer app object directly (never by shelling out per command).

Two directions fail RED:

  (a) a snapshotted command disappears, is renamed, or drops a required
      parameter — that is a compatibility break;
  (b) a NEW command appears that is not in the snapshot — an explicit, reviewed
      addition, so the snapshot must be regenerated on purpose.

Regenerate after an intentional surface change:

    python -m tests.test_fp_cli_snapshot
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from manju.cli import app

SNAPSHOT_PATH = Path(__file__).resolve().parent / "fixtures" / "cli_surface.json"

_REGEN_HINT = (
    "If this is an intentional CLI surface change, regenerate the frozen "
    "snapshot with:  python -m tests.test_fp_cli_snapshot"
)


def generate_cli_surface() -> dict[str, dict]:
    """Walk the typer app (via its click command tree) and return the frozen
    inventory ``{command_path: {"params": [required only], "exists": True}}``.

    Deterministic: command paths and params are sorted, so the snapshot diff is
    stable across runs and platforms.
    """
    click_cmd = typer.main.get_command(app)
    surface: dict[str, dict] = {}

    def walk(cmd, path: list[str]) -> None:
        children = getattr(cmd, "commands", None)
        if children:  # a group / sub-app — recurse into its commands
            for name, child in children.items():
                walk(child, path + [name])
            return
        required = sorted(
            p.name for p in cmd.params if getattr(p, "required", False)
        )
        surface[" ".join(path)] = {"params": required, "exists": True}

    walk(click_cmd, [])
    return dict(sorted(surface.items()))


def _load_snapshot() -> dict[str, dict]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------- (a)


def test_snapshot_file_exists():
    assert SNAPSHOT_PATH.exists(), (
        f"missing CLI surface snapshot at {SNAPSHOT_PATH}. {_REGEN_HINT}")


def test_no_snapshotted_command_removed_or_renamed():
    """(a) Every frozen command still exists in the live app."""
    live = generate_cli_surface()
    snapshot = _load_snapshot()
    missing = sorted(cmd for cmd in snapshot if cmd not in live)
    assert not missing, (
        "these frozen CLI commands were REMOVED or RENAMED — that is a "
        f"compatibility break: {missing}. If the removal is intentional and "
        f"reviewed, {_REGEN_HINT}"
    )


def test_no_snapshotted_command_dropped_a_required_param():
    """(a) A frozen command still carries every required param it was frozen
    with (dropping/renaming a required param breaks existing callers)."""
    live = generate_cli_surface()
    snapshot = _load_snapshot()
    broken: list[str] = []
    for cmd, spec in snapshot.items():
        if cmd not in live:
            continue  # covered by the removal test
        frozen = set(spec["params"])
        current = set(live[cmd]["params"])
        dropped = frozen - current
        if dropped:
            broken.append(f"{cmd!r} dropped required params {sorted(dropped)}")
    assert not broken, (
        "required-parameter compatibility break(s): " + "; ".join(broken)
        + f". {_REGEN_HINT}"
    )


# --------------------------------------------------------------------- (b)


def test_no_new_unsnapshotted_command():
    """(b) A new command must be added to the snapshot on purpose."""
    live = generate_cli_surface()
    snapshot = _load_snapshot()
    added = sorted(cmd for cmd in live if cmd not in snapshot)
    assert not added, (
        "these commands exist in the app but are NOT in the frozen snapshot — "
        f"new public surface must be reviewed + frozen explicitly: {added}. "
        f"{_REGEN_HINT}"
    )


def test_snapshot_did_not_gain_a_new_required_param():
    """A newly-required param on a frozen command is also a surface change that
    must be reviewed (it can break callers that omitted the now-required flag)."""
    live = generate_cli_surface()
    snapshot = _load_snapshot()
    changed: list[str] = []
    for cmd, spec in snapshot.items():
        if cmd not in live:
            continue
        frozen = set(spec["params"])
        current = set(live[cmd]["params"])
        gained = current - frozen
        if gained:
            changed.append(f"{cmd!r} newly requires {sorted(gained)}")
    assert not changed, (
        "command(s) gained a required parameter: " + "; ".join(changed)
        + f". {_REGEN_HINT}"
    )


def test_snapshot_meets_the_audited_floor():
    """112 was the audited CLI surface at freeze time (2026-07-12). The surface
    may GROW via reviewed regeneration (a later loop adding a command and
    regenerating is exactly the (b)-path), so this is a FLOOR, not an equality.

    It is the ONLY guard against a TRUNCATED regeneration: tests (a)/(b) compare
    the live app against the snapshot, so if someone regenerated the snapshot
    AFTER a mass removal, live and snapshot would agree and (a)/(b) would pass —
    a snapshot that dropped below the audited floor is the tell that regeneration
    happened on top of a removal that should have been reviewed."""
    assert len(_load_snapshot()) >= 112


def _write_snapshot() -> None:
    surface = generate_cli_surface()
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(surface, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(surface)} commands to {SNAPSHOT_PATH}")


if __name__ == "__main__":
    _write_snapshot()

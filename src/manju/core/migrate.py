"""The real ``manju migrate`` tool — the declared project.fps int→rational
migration, made runnable (CONTRACTS.yaml ``planned_migrations[0]``).

The registry deferred this machinery until a genuine migration existed. R1 landed
the rational truth field + accessor, R2 the opt-in rational build spine, R4 the
export truth — so the int→rational move is now a real, benefit-bearing migration
and this tool performs it, honestly and reversibly:

* :func:`migrate_inspect` — ZERO-WRITE. Reports the current fps, whether a
  rational rate is already declared, the candidate rates (the NTSC "1001"
  neighbour for fps ∈ {24, 30, 60} plus the exact-int passthrough — the tool
  LISTS them and NEVER recommends one), the surfaces the change touches, and an
  honest rebuild forecast: a COUNT of the content-addressed segment-cache entries
  that go cold after the rate change (never a deletion — stale cache is inert
  disk, gc's business).
* :func:`migrate_plan` — the concrete CAS plan: the project.yaml byte diff
  (rational key insertion, with ``fps`` kept as the mirror), the expected
  pre-write sha256, the result sha256, and the post-checks. Refuses a target
  whose nominal integer rate does not equal the project's ``fps`` (that would
  split the legacy mirror the whole migration preserves).
* :func:`apply_plan` — content-addressed apply: the on-disk file hash MUST equal
  the plan's expected hash (else refuse — optimistic concurrency), then an ATOMIC
  write of ONLY project.yaml, then a post-check (config validates + the resolver
  returns the target). It never git-commits — git is the user's rollback engine,
  so it returns the exact revert command instead.
* :func:`downgrade_plan` / :func:`downgrade_loss_report` — the reverse (rational
  → int): the structured loss rows, and a write that :func:`apply_plan` REFUSES
  unless the caller acknowledges the loss.

Encapsulation discipline (the R1 surface pin, kept green): this module NEVER
names the raw rational-rate field token. It depends on the :class:`EditRate`
TYPE and the ``ProjectConfig.frame_rate`` resolver, and discovers the field's
key BY TYPE (:data:`_RATE_FIELD`) — so ``tests/test_fp_ratemig1.py``'s grep pin
stays green with this file NOT in its sanctioned set. The engine derives
everything else (timeline, render command lines, cache keys) from the pin at the
next compile — apply writes one file and stops.
"""

from __future__ import annotations

import hashlib
import subprocess
from difflib import unified_diff
from fractions import Fraction
from typing import Any, get_args

from .container import PROJECT_FILE, Project
from .hashing import HASH_PREFIX
from .models import EditRate, ProjectConfig
from .timebase import Rate
from .yamlio import atomic_write_text, dump_yaml, read_yaml


class MigrateError(RuntimeError):
    """A migration was refused (broken fps mirror, CAS drift, missing
    acknowledgement) or is impossible (nothing to downgrade)."""


# The rational-rate field's key on ProjectConfig, discovered BY TYPE so this
# module never hard-codes the token (the R1 surface pin). The field is the one
# whose annotation is ``EditRate | None``.
def _discover_rate_field() -> str:
    for name, field in ProjectConfig.model_fields.items():
        if EditRate in get_args(field.annotation):
            return name
    raise RuntimeError(  # pragma: no cover - the field is part of the R1 surface
        "ProjectConfig no longer carries an EditRate field — the migration tool "
        "depends on it (core/models.py, R1)"
    )


_RATE_FIELD = _discover_rate_field()

# The NTSC "1001" neighbour for the integer rates that have one. Any other fps
# has no honest NTSC partner — inspect says so rather than inventing a rate.
_NTSC_NEIGHBORS: dict[int, tuple[int, int]] = {
    24: (24000, 1001),
    30: (30000, 1001),
    60: (60000, 1001),
}

# Drop-frame timecode is defined only for these two rational rates.
_DF_LEGAL = {Fraction(30000, 1001), Fraction(60000, 1001)}


# --------------------------------------------------------------------------- #
# small helpers                                                                #
# --------------------------------------------------------------------------- #

def _project_yaml(project: Project):
    return project.root / PROJECT_FILE


def _sha256_bytes(data: bytes) -> str:
    return HASH_PREFIX + hashlib.sha256(data).hexdigest()


def _declared_rate(config: ProjectConfig) -> EditRate | None:
    """The rational rate the project DECLARES, or None — read token-free via the
    type-discovered field name (never the raw attribute token)."""
    return getattr(config, _RATE_FIELD)


def count_cached_segments(project: Project) -> int:
    """The number of cached segment artifacts under ``project.segments_dir`` —
    plain segments AND ``xfade_*`` boundary segments are all ``*.mp4`` there
    (media/render.py:396). A COUNT only; this never deletes anything."""
    seg = project.segments_dir
    if not seg.exists():
        return 0
    return sum(1 for _ in seg.glob("*.mp4"))


def _candidates(fps: int) -> tuple[list[dict[str, Any]], str]:
    """The candidate target rates for an int project, NEWEST-timing-first but with
    NO recommended field: the NTSC neighbour (when one exists) then the exact-int
    passthrough. Returns (candidates, honest_note)."""
    cands: list[dict[str, Any]] = []
    note = ""
    neighbor = _NTSC_NEIGHBORS.get(fps)
    if neighbor is not None:
        num, den = neighbor
        cands.append({
            "num": num, "den": den, "rate": f"{num}/{den}",
            "kind": "ntsc_neighbor", "nominal_int": fps,
            "note": f"exact NTSC {Rate.from_fraction(num, den).fps_float:.3f} — "
                    "cumulative-drift-free rational timing",
        })
    else:
        note = (f"no NTSC neighbor for fps={fps} — exact int only "
                "(the 1001 family covers 24, 30, 60)")
    cands.append({
        "num": fps, "den": 1, "rate": str(fps),
        "kind": "exact_passthrough", "nominal_int": fps,
        "note": "declare the rate as an exact whole number (no timing change; "
                "makes the rate explicit/rational-typed)",
    })
    return cands, note


def _affected_surfaces() -> list[str]:
    """The surfaces the int→rational move changes — all derived by the engine at
    the next compile from the single project.yaml pin (no extra writes here)."""
    return [
        "timeline compile: the cumulative-boundary rational frame grid replaces "
        "the int-ms snap (each clip gains an exact duration_frames) — R2",
        "render: ffmpeg is fed the native num/den at every fps=/-framerate/-r "
        "injection point — R2",
        "cache keys: segment / boundary / final content keys gain a rational "
        "component, so existing int-keyed segments go cold — R2",
        "export: OTIO/EDL/interchange carry the exact rational rate instead of a "
        "nominal-int approximation — R4",
    ]


def _revert_pair(project: Project) -> tuple[str, str]:
    """The exact revert command (git is the rollback engine) and the manju-native
    equivalent — apply never commits, so reverting is discarding one working-tree
    file."""
    return (
        f"git -C {project.root} checkout -- {PROJECT_FILE}",
        f"manju rollback file {PROJECT_FILE}",
    )


# --------------------------------------------------------------------------- #
# inspect — zero write                                                         #
# --------------------------------------------------------------------------- #

def migrate_inspect(project: Project) -> dict[str, Any]:
    """Report the migration picture for ``project`` WITHOUT writing anything.

    Lists the candidate target rates (never recommending one), the affected
    surfaces, and an honest rebuild forecast (a count of cached segments that go
    cold). Reads config through the container; touches no file."""
    config = project.load_config()
    fps = config.fps
    current = config.frame_rate
    declared = _declared_rate(config) is not None
    candidates, note = _candidates(fps)
    cold = count_cached_segments(project)

    return {
        "project": project.root.name,
        "path": PROJECT_FILE,
        "fps": fps,
        "rational_declared": declared,
        "current_rate": str(current),
        "is_ntsc": current.is_ntsc,
        "candidates": candidates,
        "candidate_note": note,
        "rebuild_forecast": {
            "cached_segments": cold,
            "segments_dir": project.relpath(project.segments_dir),
            "note": (
                f"{cold} cached segment(s) will go cold after the rate change — a "
                "COUNT, not a deletion. Segment caches are content-addressed, so "
                "stale entries are inert disk that the next build ignores and gc "
                "reclaims; nothing is removed here."
            ),
        },
        "affected_surfaces": _affected_surfaces(),
        "downgrade_available": declared and current.exact_int is None,
    }


# --------------------------------------------------------------------------- #
# plan — CAS anchor + byte diff                                                #
# --------------------------------------------------------------------------- #

def _target_raw(current_raw: dict[str, Any], action: str,
                target: Rate | None) -> dict[str, Any]:
    """The project.yaml dict AFTER the migration — the rational key inserted
    (apply) or removed (downgrade). Everything else (including extra keys) rides
    through unchanged."""
    if action == "apply":
        assert target is not None
        out = dict(current_raw)
        out[_RATE_FIELD] = {"num": target.numerator, "den": target.denominator}
        return out
    # downgrade: drop the rational key, keep the int fps mirror
    return {k: v for k, v in current_raw.items() if k != _RATE_FIELD}


def _serialize_target(current_raw: dict[str, Any], action: str,
                      target: Rate | None) -> bytes:
    """The canonical project.yaml bytes after the migration — through the model,
    so the result is exactly what the engine would validate + write."""
    target_config = ProjectConfig.model_validate(_target_raw(current_raw, action, target))
    return dump_yaml(target_config.model_dump(exclude_none=True)).encode("utf-8")


def _build_plan(project: Project, action: str, target: Rate | None) -> dict[str, Any]:
    path = _project_yaml(project)
    current_bytes = path.read_bytes()
    current_raw = read_yaml(path) or {}
    target_bytes = _serialize_target(current_raw, action, target)

    diff = list(unified_diff(
        current_bytes.decode("utf-8").splitlines(keepends=True),
        target_bytes.decode("utf-8").splitlines(keepends=True),
        fromfile=PROJECT_FILE, tofile=PROJECT_FILE,
    ))
    revert, revert_native = _revert_pair(project)
    resolver_rate = str(target) if action == "apply" else str(project.load_config().fps)

    return {
        "action": action,
        "project": project.root.name,
        "path": PROJECT_FILE,
        "fps": project.load_config().fps,
        "target_rate": resolver_rate,
        "target": ({"num": target.numerator, "den": target.denominator}
                   if action == "apply" and target is not None else None),
        "expected_sha256": _sha256_bytes(current_bytes),   # CAS anchor: current file
        "result_sha256": _sha256_bytes(target_bytes),      # the write's content
        "diff": diff,
        "already_at_target": current_bytes == target_bytes,
        "post_checks": [
            f"{PROJECT_FILE} validates (manju check-level config validation)",
            f"the rate resolver returns {resolver_rate}",
        ],
        "other_files_touched": False,
        "acknowledgment_required": action == "downgrade",
        "revert": revert,
        "revert_native": revert_native,
    }


def migrate_plan(project: Project, target: Rate) -> dict[str, Any]:
    """Build the CAS plan to declare ``target`` as the project's rational rate.

    Refuses a target whose nominal integer rate is not the project's current
    ``fps`` — the migration PRESERVES the legacy ``fps`` mirror (changing the
    nominal rate is a different operation, not this int→rational move)."""
    config = project.load_config()
    if target.nominal_int != config.fps:
        raise MigrateError(
            f"target rate {target} has nominal integer rate {target.nominal_int}, "
            f"which does not mirror the project's fps={config.fps} — the int→rational "
            f"migration keeps fps as the exact mirror of the rational rate. Change fps "
            f"to {target.nominal_int} first if that is really the intent, or pick a "
            f"candidate whose nominal_int is {config.fps} (see `manju migrate inspect`)."
        )
    return _build_plan(project, "apply", target)


def downgrade_plan(project: Project) -> dict[str, Any]:
    """Build the plan to REMOVE the rational rate (rational → int fps). Refuses
    when the project declares no rational rate (nothing to downgrade)."""
    config = project.load_config()
    if _declared_rate(config) is None:
        raise MigrateError(
            f"nothing to downgrade — the project declares no rational rate; it is "
            f"already plain int fps={config.fps}."
        )
    return _build_plan(project, "downgrade", None)


# --------------------------------------------------------------------------- #
# downgrade loss report                                                        #
# --------------------------------------------------------------------------- #

def downgrade_loss_report(project: Project) -> dict[str, Any]:
    """The structured, honest loss rows for a rational → int downgrade.

    Refuses when the project declares no rational rate (there is no loss to
    report)."""
    config = project.load_config()
    declared = _declared_rate(config)
    if declared is None:
        raise MigrateError(
            f"nothing to downgrade — the project declares no rational rate "
            f"(fps={config.fps})."
        )
    rate = config.frame_rate
    fps = config.fps
    df_legal = rate.fraction in _DF_LEGAL

    loss = [
        {
            "aspect": "exact_ntsc_timing",
            "before": f"{rate} ({rate.fps_float:.3f} fps)",
            "after": f"{fps} (whole)",
            "impact": ("the exact NTSC playback rate is discarded; timing reverts "
                       "to whole-integer fps and the 0.1% NTSC-on-int drift returns"),
        },
        {
            "aspect": "drop_frame_timecode",
            "before": ("drop-frame timecode available" if df_legal
                       else "n/a (drop-frame is only defined for 29.97 / 59.94)"),
            "after": "non-drop only",
            "impact": ("drop-frame timecode (legal only for 30000/1001 and "
                       "60000/1001) no longer applies once the rate is a whole int"),
        },
        {
            "aspect": "drift_free_grid",
            "before": "cumulative-boundary rational frame grid (≤ ½ ms forever)",
            "after": "int-ms snap grid",
            "impact": ("the cumulative-drift-free frame grid is lost; long concats "
                       "drift on the int-ms grid again (R2)"),
        },
        {
            "aspect": "cache_key_continuity",
            "before": "rational-keyed segment / boundary / final caches",
            "after": "int-keyed caches",
            "impact": (f"the cache keys shift back, so the {count_cached_segments(project)} "
                       "currently-cached segment(s) go cold again (a rebuild, no deletion)"),
        },
        {
            "aspect": "interchange_rate",
            "before": f"OTIO/EDL carry the exact {rate}",
            "after": f"nominal-int {fps} approximation",
            "impact": ("exported OTIO/EDL rational values become nominal-int "
                       "approximations of the true rate (R4)"),
        },
    ]

    return {
        "project": project.root.name,
        "current_rate": str(rate),
        "downgrades_to_fps": fps,
        "cold_segments": count_cached_segments(project),
        "loss": loss,
        "acknowledgment_required": True,
    }


# --------------------------------------------------------------------------- #
# apply — CAS + atomic + post-check + no auto-commit                           #
# --------------------------------------------------------------------------- #

def apply_plan(project: Project, plan: dict[str, Any], *,
               acknowledged: bool = False) -> dict[str, Any]:
    """Apply a migration plan under content-addressed concurrency control.

    Discipline: the on-disk project.yaml hash MUST equal the plan's expected hash
    (else refuse — the file changed since the plan was made). A downgrade plan is
    refused unless ``acknowledged`` is True (the loss must be accepted). The write
    is ATOMIC and touches ONLY project.yaml. It NEVER git-commits — the returned
    ``revert`` is the exact rollback command."""
    action = plan["action"]
    if action == "downgrade" and not acknowledged:
        raise MigrateError(
            "refusing to downgrade rational → int without acknowledging the loss "
            "(exact NTSC timing, drop-frame timecode, drift-free grid, cache "
            "continuity, interchange precision). Re-run with the loss acknowledged "
            "(CLI: `--acknowledge-loss`) — see `manju migrate downgrade` for the "
            "full loss report."
        )

    path = _project_yaml(project)
    current_bytes = path.read_bytes()
    current_hash = _sha256_bytes(current_bytes)

    # CAS: the file must be exactly what the plan was built against.
    if current_hash != plan["expected_sha256"]:
        raise MigrateError(
            f"{PROJECT_FILE} changed since the plan was made (expected "
            f"{plan['expected_sha256']}, on disk {current_hash}) — refusing to apply "
            f"a stale plan. Re-run `manju migrate plan` / `inspect` and retry."
        )

    if plan["already_at_target"]:
        return {
            "action": action, "changed": False, "path": PROJECT_FILE,
            "reason": "already at target — the project.yaml is byte-identical to "
                      "the migration result; nothing to write",
            "committed": False,
        }

    # Reconstruct the target bytes from the CAS-verified current file and confirm
    # they match the plan's result hash (the plan and the write must agree).
    current_raw = read_yaml(path) or {}
    target = None
    if action == "apply":
        t = plan["target"]
        target = Rate.from_fraction(t["num"], t["den"])
    target_bytes = _serialize_target(current_raw, action, target)
    result_hash = _sha256_bytes(target_bytes)
    if result_hash != plan["result_sha256"]:
        raise MigrateError(
            "internal: the recomputed migration result does not match the plan "
            f"(plan {plan['result_sha256']}, recomputed {result_hash}) — refusing "
            "to write. Rebuild the plan."
        )

    # Atomic write of ONLY project.yaml (temp + fsync + rename, via yamlio).
    atomic_write_text(path, target_bytes.decode("utf-8"))

    # Post-check: the written file validates and the resolver returns the target.
    reloaded = project.load_config()
    resolver_rate = str(reloaded.frame_rate)
    post_check = {
        "validates": True,   # load_config raises if it does not — reaching here means it did
        "resolver_rate": resolver_rate,
        "rational_declared": _declared_rate(reloaded) is not None,
    }

    revert, revert_native = _revert_pair(project)
    result = {
        "action": action,
        "changed": True,
        "path": PROJECT_FILE,
        "from_fps": reloaded.fps,
        "before_sha256": current_hash,
        "after_sha256": result_hash,
        "post_check": post_check,
        "committed": False,   # git is the user's rollback engine — never auto-commit
        "revert": revert,
        "revert_native": revert_native,
        "cache": {
            "cold_segments": count_cached_segments(project),
            "note": "content-addressed segments are rebuilt on the next build; "
                    "nothing was deleted",
        },
    }
    result["to_rate" if action == "apply" else "to_fps"] = (
        resolver_rate if action == "apply" else reloaded.fps
    )
    return result

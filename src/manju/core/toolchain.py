"""``manju.toolchain-manifest/v1`` — record-only reproducibility evidence
(roadmap §7.7).

A build that is deterministic in *inputs* can still change bytes when the
*toolchain* under it moves: a new ffmpeg, a swapped system font, a different
Python. This module captures those machine facts as a derived, deletable
evidence document so drift between two runs is **explainable** — which
toolchain fact changed, from what, to what.

Scope ruling (this loop): RECORD-ONLY
-------------------------------------
Nothing here is wired into content keys, build caching, resume, or
authorization — and a grep-pinned test keeps it that way (no
build/core/providers/runtime path reads ``reports/toolchain/`` or imports this
module). The document is evidence a human or dashboard reads; deleting it
loses the report, never an output.

Declared next step — LANDED as STRICTLY OPT-IN (S4, user item 7), mirroring
the fps migration pattern in :mod:`manju.core.timebase`: 影响输出字节的工具进入
内容键 — the two byte-affecting facts (the ffmpeg ``-version`` first line and
the drawtext font content hash) can now enter the render cache keys, but ONLY
when a project explicitly lists them in ``project.yaml``'s
``cache_toolchain_keys`` (validated to EXACTLY {"ffmpeg", "fonts"} — anything
else would only cause meaningless rebuilds and is rejected at load). A project
that never opts in keeps byte-identical keys forever. The single consumer seam
is ``media/render.py``'s key-component helper, which reads the process-cached
wrappers below; the manifest document itself STAYS record-only — nothing in
``build``/``core``/``providers``/``runtime`` reads the derived report or
imports this module (the grep-pinned inertness test still holds verbatim).

Design rules honoured here
--------------------------
* **Facts vs volatile.** ``facts`` is everything digestable — deterministic on
  an unchanged machine. ``volatile`` is EMPTY this loop: no timestamps, no
  counters, so two calls yield a byte-identical document and digest.
* **Absence is a fact, never a crash.** A missing tool records the string
  ``"missing"``; an uninstalled dep records ``"absent"``; an unlocatable font
  records ``"unknown"``. Every probe is wrapped and short-timeout'd.
* **No host identity.** No hostname, no username, no absolute path anywhere in
  the document — fonts are basename + content hash; ``LANG`` is presence-only;
  OS facts are ``system/release/machine``. A scan test pins this.
* **Digest over facts only.** :func:`manifest_digest` hashes the ``facts``
  block through :func:`manju.core.hashing.hash_value`, so envelope or volatile
  edits never move it and any fact change always does.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from .hashing import hash_file, hash_value
from .yamlio import atomic_write_text

__all__ = [
    "SCHEMA",
    "cached_drawtext_font_hash",
    "cached_tool_version_line",
    "manifest_digest",
    "toolchain_dir",
    "toolchain_drift",
    "toolchain_manifest",
    "write_toolchain_manifest",
]

SCHEMA = "manju.toolchain-manifest/v1"

# Honesty sentinels — one per kind of gap (they are facts, not errors).
MISSING = "missing"    # a PATH tool that is not installed
ABSENT = "absent"      # a Python dep that is not installed
UNKNOWN = "unknown"    # a font/fact we could not locate — never guessed

# Tools whose ``-version`` FIRST line is captured verbatim (the first line is
# name+version+copyright; the later ``configuration:`` line carries build
# --prefix paths and is deliberately never read).
_VERSION_TOOLS = ("ffmpeg", "ffprobe")

# Optional extras the test-suite / render ladder gate on — presence-only.
_OPTIONAL_TOOLS = ("tesseract", "chromium")

# Key deps: import-name → distribution name. Present-or-absent, never a crash.
_KEY_DEPS: tuple[tuple[str, str], ...] = (
    ("pydantic", "pydantic"),
    ("typer", "typer"),
    ("yaml", "PyYAML"),
    ("PIL", "Pillow"),
    ("opentimelineio", "OpenTimelineIO"),
    ("hypothesis", "hypothesis"),
)

_VERSION_TIMEOUT_S = 10.0
_GIT_TIMEOUT_S = 5.0


# --------------------------------------------------------------- collectors


def _manju_version() -> str:
    """manju version: importlib.metadata → ``git describe`` → ``"unknown"``."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("manju")
        except PackageNotFoundError:
            pass
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S,
        )
        described = (out.stdout or "").strip()
        if out.returncode == 0 and described:
            return described
    except (OSError, subprocess.SubprocessError):
        pass
    return UNKNOWN


def _tool_version_line(name: str) -> str:
    """FIRST line of ``<tool> -version`` verbatim; ``"missing"`` when the tool
    is absent or anything at all goes wrong (absence is a fact, not an error)."""
    exe = shutil.which(name)
    if not exe:
        return MISSING
    try:
        out = subprocess.run(
            [exe, "-version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_VERSION_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return MISSING
    for stream in (out.stdout, out.stderr):
        lines = (stream or "").splitlines()
        if lines and lines[0].strip():
            return lines[0].strip()
    return MISSING


def _dep_version(module: str, dist: str) -> str:
    """A version string when the dep is installed, ``"absent"`` when not.
    importlib.metadata by distribution first, then a light import fallback."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version(dist)
        except PackageNotFoundError:
            pass
    except Exception:
        pass
    try:
        mod = __import__(module)
    except Exception:
        return ABSENT
    return str(getattr(mod, "__version__", "present"))


def _optional_tool_present(name: str) -> bool:
    """Presence-only for the extras the suite gates on. Chromium goes through
    the renderer's own locator (CHROME_BIN / PATH / Playwright dir) so the fact
    matches what a card render would actually find; only the BOOL is recorded —
    the resolved path never enters the document."""
    if name == "chromium":
        try:
            from ..media.html_card import find_chromium

            return find_chromium() is not None
        except Exception:
            return False
    return shutil.which(name) is not None


def _encoder_facts() -> dict[str, bool]:
    """W5.4: the encode-profile encoder presence facts, DELEGATED to the one
    owner (``media/ffmpeg.encoder_inventory`` — the module that owns "what can
    this ffmpeg do"), exactly like ``_font_inventory`` delegates to
    ``media/card.find_font``. Unimportable media ⇒ honest all-absent."""
    try:
        from ..media.ffmpeg import _encoder_inventory_uncached

        # the manifest keeps probing fresh (see the S4 note below): a manifest
        # written after a live ffmpeg swap must see the new truth mid-process.
        return _encoder_inventory_uncached()
    except Exception:
        # literal mirror of ("libx264", *HW_ENCODER_CANDIDATES) — the owner
        # module just failed to import, so its constant is unreachable too
        return {n: False for n in
                ("libx264", "h264_amf", "h264_nvenc", "h264_qsv")}


def _font_inventory() -> dict[str, Any]:
    """The fonts the renderer ACTUALLY uses, as basename + content hash.

    Audit trail: both burn surfaces resolve their font file through one
    locator — :func:`manju.media.card.find_font` (the §8.4 drawtext card AND
    ``media/render.py``'s caption/text-overlay burn import it). The HTML card
    path names CSS families that Chromium resolves internally via fontconfig,
    so it exposes no file for us to hash — the drawtext locator is the honest
    machine fact. Unlocatable ⇒ ``"unknown"``; the absolute path NEVER enters
    the document (basename + sha256 only).
    """
    try:
        from ..media.card import find_font

        path = find_font()
    except Exception:
        path = None
    if path is None:
        return {"drawtext_cjk": UNKNOWN}
    try:
        return {"drawtext_cjk": {"basename": Path(path).name,
                                 "sha256": hash_file(Path(path))}}
    except OSError:
        return {"drawtext_cjk": UNKNOWN}


# ------------------------------------------------- process-cached facts (S4)
# The opt-in key wiring (media/render's toolchain key component) reads facts
# through these wrappers so N cache-key computations cost exactly ONE probe per
# process (a call-count spy pins this in tests/test_fp_toolkeys.py). They are
# thin lru_cache shells over the SAME collectors the manifest records — never a
# parallel account of the machine. ``toolchain_manifest`` itself deliberately
# keeps probing fresh (it is the record; a manifest written after a live
# ffmpeg upgrade must see the new truth even mid-process).


@lru_cache(maxsize=None)
def cached_tool_version_line(name: str) -> str:
    """Process-cached :func:`_tool_version_line`: the ``-version`` FIRST line
    verbatim, or the honest ``"missing"`` — one subprocess probe per process."""
    return _tool_version_line(name)


@lru_cache(maxsize=None)
def cached_drawtext_font_hash() -> str:
    """Process-cached drawtext font content hash (the §8.4 burn font both burn
    surfaces resolve via ``media/card.find_font``): the ``sha256:...`` string,
    or the honest ``"unknown"`` when no font is locatable — one hash-the-file
    probe per process."""
    entry = _font_inventory().get("drawtext_cjk")
    if isinstance(entry, dict) and isinstance(entry.get("sha256"), str):
        return entry["sha256"]
    return UNKNOWN


# --------------------------------------------------------------- the document


def toolchain_manifest(project: Any = None) -> dict[str, Any]:
    """Build the ``manju.toolchain-manifest/v1`` document for THIS machine.

    ``project`` is accepted (and currently unused) so a future project-scoped
    inventory — e.g. project-bundled fonts — can bind here without a signature
    break. Facts are machine-level and deterministic on an unchanged machine:
    calling twice yields an identical document and digest. ``volatile`` is
    empty this loop by design (timestamps stay OUT).
    """
    del project  # machine facts only, this loop — see docstring
    facts: dict[str, Any] = {
        "manju": {"version": _manju_version()},
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "arch": platform.machine(),
        },
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "tools": {name: _tool_version_line(name) for name in _VERSION_TOOLS},
        # W5.4 additive fact block: which encode-profile encoders THIS ffmpeg
        # lists (libx264 floor + the h264 hw family). Record-only like every
        # other fact here — drift names a gained/lost hw encoder; never a
        # build input (S4 keys read the -version line, not the manifest).
        "encoders": _encoder_facts(),
        "optional_tools": {name: _optional_tool_present(name)
                           for name in _OPTIONAL_TOOLS},
        "deps": {dist: _dep_version(module, dist) for module, dist in _KEY_DEPS},
        "fonts": _font_inventory(),
        "locale": {
            "filesystem_encoding": sys.getfilesystemencoding(),
            "lang_set": "LANG" in os.environ,   # presence-only, value never stored
        },
    }
    return {
        "schema": SCHEMA,
        "facts": facts,
        "volatile": {},                          # record-only loop: nothing volatile
        "manifest_digest": hash_value(facts),
    }


def manifest_digest(doc: dict[str, Any]) -> str:
    """Digest over the ``facts`` block ONLY (canonical JSON, sha256-prefixed).

    Envelope fields (``schema``, ``volatile``, an embedded digest) never move
    it; any fact change always does. A document without a ``facts`` mapping is
    malformed and refused."""
    facts = doc.get("facts") if isinstance(doc, dict) else None
    if not isinstance(facts, dict):
        raise ValueError("toolchain manifest has no 'facts' block to digest")
    return hash_value(facts)


# --------------------------------------------------------------- storage (derived)


def toolchain_dir(project: Any) -> Path:
    """``reports/toolchain/`` — a deletable derived projection, never a build,
    cache, resume, or authorization input (grep-pinned by the tests)."""
    return Path(project.root) / "reports" / "toolchain"


def write_toolchain_manifest(project: Any, doc: dict[str, Any]) -> Path:
    """Materialise the manifest content-addressed by its facts digest:
    ``reports/toolchain/<digest-hex>.json``. Atomic, byte-stable (sorted keys),
    deletable. The filename digest is always RECOMPUTED from ``facts``; an
    embedded ``manifest_digest`` that disagrees is a tampered/hand-edited
    document and is refused, never silently re-filed."""
    computed = manifest_digest(doc)
    embedded = doc.get("manifest_digest")
    if embedded is not None and embedded != computed:
        raise ValueError(
            "embedded manifest_digest does not match the facts block "
            f"(embedded {embedded!r}, computed {computed!r}) — refusing to "
            "store a tampered manifest"
        )
    path = toolchain_dir(project) / f"{computed.split(':')[-1]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path, json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return path


# --------------------------------------------------------------- drift (pure)


def _flatten(node: Any, prefix: str, out: dict[str, Any]) -> None:
    """Depth-first flatten of nested fact dicts to dotted-path leaves."""
    if isinstance(node, dict):
        for key in node:
            _flatten(node[key], f"{prefix}.{key}" if prefix else str(key), out)
        return
    out[prefix] = node


def _facts_of(doc: Any) -> dict[str, Any]:
    """Accept a full manifest document or a bare facts mapping."""
    if isinstance(doc, dict) and isinstance(doc.get("facts"), dict):
        return doc["facts"]
    if isinstance(doc, dict):
        return doc
    raise ValueError("toolchain_drift expects manifest documents or facts mappings")


def toolchain_drift(old_doc: dict[str, Any], new_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """PURE compare of two manifests → structured rows for every changed fact.

    Each row is ``{"fact": <dotted path>, "old": ..., "new": ...}``; a fact
    present on only one side carries ``None`` for the missing side (``None``
    never occurs as a real fact value — gaps are the strings ``"missing"`` /
    ``"absent"`` / ``"unknown"``). Rows are sorted by fact path so the drift
    surface the dashboard/archive loops consume is deterministic. No I/O, no
    subprocess — identical inputs yield ``[]``.
    """
    old_flat: dict[str, Any] = {}
    new_flat: dict[str, Any] = {}
    _flatten(_facts_of(old_doc), "", old_flat)
    _flatten(_facts_of(new_doc), "", new_flat)
    rows: list[dict[str, Any]] = []
    for fact in sorted(old_flat.keys() | new_flat.keys()):
        old_v = old_flat.get(fact)
        new_v = new_flat.get(fact)
        if fact in old_flat and fact in new_flat and old_v == new_v:
            continue
        rows.append({"fact": fact,
                     "old": old_v if fact in old_flat else None,
                     "new": new_v if fact in new_flat else None})
    return rows

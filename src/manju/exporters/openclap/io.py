"""Reading (raw-preserving) and writing ``.clap`` files, plus ``inspect``.

A ``.clap`` file is a gzip-compressed single YAML document whose top level is a
fixed-order array: ``[header, meta, *workflows, *entities, *scenes, *segments]``.
This module is the ONLY place that touches those bytes:

- :func:`read_clap` — stdlib ``gzip`` + ``yaml.safe_load`` behind hard resource
  limits (size, streamed decompression cap, item cap, nesting cap, strict
  UTF-8). It fails CLOSED with structured :class:`~.model.Diagnostic` entries on
  any breach or structural fault and NEVER executes anything (``safe_load``
  only, no custom constructors). Header counts are VERIFIED, not trusted.
- :func:`write_clap` — re-serializes ``raw_items`` (with any patched fields),
  recomputes header counts from actual section sizes, gzips deterministically
  (``mtime=0``) and writes atomically. The only silent normalization allowed is
  correcting header counts, which is reported in the returned diagnostics.
- :func:`inspect_clap` — a never-writing summary (counts, meta, per-category
  histogram, locator histogram, unknown-field stats, diagnostics).

Security posture: external URLs / data URIs / paths are LOCATORS, never content
identity — nothing here dereferences a remote URL or reads an absolute path.
"""

from __future__ import annotations

import gzip
import os
import tempfile
import zlib
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from . import profile
from .model import (
    DEFAULT_LIMITS,
    ClapDocument,
    ClapEntity,
    ClapHeader,
    ClapLimits,
    ClapMeta,
    ClapReadError,
    ClapScene,
    ClapSegment,
    ClapWorkflow,
    Diagnostic,
)

_GZIP_MAGIC = b"\x1f\x8b"
_READ_CHUNK = 1 << 16  # 64 KiB streaming window for the decompression-bomb guard


# --------------------------------------------------------------- atomic bytes


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Binary sibling of ``core.yamlio.atomic_write_text``: temp + ``os.replace``.

    The gzip ``.clap`` payload is a binary artifact, so it gets the same
    crash-safety discipline every truth file gets — a durable destination only
    ever receives a complete file (``media/ffmpeg.py::atomic_output`` is the
    binary precedent). Directory-entry fsync is best-effort and degrades
    silently on platforms that cannot open a directory fd.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
            finally:
                os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------- read


def _fail(message: str, diagnostics: list[Diagnostic]) -> ClapReadError:
    """Build (do not raise) a fail-closed error carrying accumulated diagnostics."""
    return ClapReadError(message, diagnostics)


def _decompress_capped(path: Path, limits: ClapLimits,
                       diagnostics: list[Diagnostic]) -> bytes:
    """Read the file to raw bytes, honoring the size + decompression caps.

    The compressed-size ceiling is checked BEFORE reading. gzip is decompressed
    by streaming fixed chunks and counting output — the count is checked after
    every chunk so a gzip bomb aborts MID-STREAM (never decompress-then-check).
    A plain (non-gzip) file is read the same way against the same cap.
    """
    try:
        compressed_size = path.stat().st_size
    except OSError as exc:
        raise _fail(f"cannot stat .clap file: {exc}", [
            *diagnostics,
            Diagnostic("error", "io_error", f"cannot stat file: {exc}", str(path)),
        ])
    if compressed_size > limits.max_compressed_bytes:
        d = Diagnostic(
            "error", "compressed_too_large",
            f"file is {compressed_size} bytes, exceeds max_compressed_bytes="
            f"{limits.max_compressed_bytes}", str(path),
        )
        raise _fail("compressed .clap exceeds size limit", [*diagnostics, d])

    with open(path, "rb") as fh:
        magic = fh.read(2)
        fh.seek(0)
        is_gzip = magic == _GZIP_MAGIC
        stream = gzip.GzipFile(fileobj=fh) if is_gzip else fh
        out = bytearray()
        try:
            while True:
                chunk = stream.read(_READ_CHUNK)
                if not chunk:
                    break
                out += chunk
                if len(out) > limits.max_decompressed_bytes:
                    d = Diagnostic(
                        "error", "decompressed_too_large",
                        "decompressed size exceeded max_decompressed_bytes="
                        f"{limits.max_decompressed_bytes} (aborted mid-stream)",
                        str(path),
                    )
                    raise _fail("decompressed .clap exceeds size limit",
                                [*diagnostics, d])
                if not is_gzip and len(out) == compressed_size:
                    break
        except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as exc:
            # zlib.error is NOT an OSError — a corrupt deflate stream must fail
            # closed with a diagnostic, not a raw traceback.
            d = Diagnostic("error", "decompress_failed",
                           f"gzip/read error: {exc}", str(path))
            raise _fail("cannot decompress .clap", [*diagnostics, d])
        finally:
            if is_gzip:
                stream.close()
    return bytes(out)


def _walk_depth(node: Any, limit: int, depth: int = 1) -> int:
    """Deepest nesting level in ``node``; short-circuits once ``limit`` is passed."""
    if depth > limit:
        return depth
    if isinstance(node, dict):
        best = depth
        for v in node.values():
            best = max(best, _walk_depth(v, limit, depth + 1))
            if best > limit:
                return best
        return best
    if isinstance(node, list):
        best = depth
        for v in node:
            best = max(best, _walk_depth(v, limit, depth + 1))
            if best > limit:
                return best
        return best
    return depth


def read_clap(path: str | Path, limits: ClapLimits = DEFAULT_LIMITS) -> ClapDocument:
    """Parse a ``.clap`` file into a raw-preserving :class:`ClapDocument`.

    Fails closed (raises :class:`ClapReadError` carrying diagnostics) on: a
    breached resource limit, non-UTF-8 bytes, a top level that is not an array
    of mappings, a missing header, or header counts inconsistent with the actual
    array length. A parse failure creates/modifies no files.
    """
    path = Path(path)
    diagnostics: list[Diagnostic] = []

    raw_bytes = _decompress_capped(path, limits, diagnostics)

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        d = Diagnostic("error", "not_utf8",
                       f"file is not valid UTF-8: {exc}", str(path))
        raise _fail(".clap must be UTF-8", [*diagnostics, d])

    try:
        data = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        # RecursionError: PyYAML's constructor recurses, so pathological nesting
        # can blow the stack BEFORE our own depth check sees the tree — that too
        # must fail closed with a diagnostic, never a raw traceback.
        d = Diagnostic("error", "yaml_error",
                       f"YAML parse error: {' '.join(str(exc).split())}", str(path))
        raise _fail("cannot parse .clap YAML", [*diagnostics, d])

    if not isinstance(data, list):
        d = Diagnostic("error", "not_an_array",
                       f"top level must be a YAML array, got {type(data).__name__}",
                       "$")
        raise _fail(".clap top level is not an array", [*diagnostics, d])

    if len(data) > limits.max_items:
        d = Diagnostic("error", "too_many_items",
                       f"top-level array has {len(data)} items, exceeds max_items="
                       f"{limits.max_items}", "$")
        raise _fail(".clap has too many items", [*diagnostics, d])

    depth = _walk_depth(data, limits.max_nesting_depth)
    if depth > limits.max_nesting_depth:
        d = Diagnostic("error", "too_deep",
                       f"nesting depth exceeds max_nesting_depth="
                       f"{limits.max_nesting_depth}", "$")
        raise _fail(".clap is nested too deeply", [*diagnostics, d])

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            d = Diagnostic("error", "item_not_mapping",
                           f"array item {i} is a {type(item).__name__}, "
                           "every .clap item must be a mapping", f"items[{i}]")
            raise _fail(".clap item is not a mapping", [*diagnostics, d])

    if len(data) < 1:
        d = Diagnostic("error", "missing_header",
                       "empty document: no header at items[0]", "items[0]")
        raise _fail(".clap has no header", [*diagnostics, d])

    header = ClapHeader(data[0])
    if header.format is None:
        diagnostics.append(Diagnostic(
            "warning", "header_no_format",
            "header has no 'format' string field", "items[0].format"))

    meta = ClapMeta(data[1]) if len(data) > 1 else ClapMeta({})
    if len(data) < 2:
        diagnostics.append(Diagnostic(
            "info", "missing_meta", "no meta at items[1]; using empty meta",
            "items[1]"))

    # --- verify header counts (reference defect #4: counts are trusted there) --
    body = data[2:]
    w = header.declared_workflows or 0
    e = header.declared_entities or 0
    s = header.declared_scenes or 0
    for name, val in (("numberOfWorkflows", w), ("numberOfEntities", e),
                      ("numberOfScenes", s)):
        if val < 0:
            d = Diagnostic("error", "negative_count",
                           f"header {name} is negative ({val})", f"items[0].{name}")
            raise _fail(".clap header count is negative", [*diagnostics, d])

    need = w + e + s
    if need > len(body):
        d = Diagnostic(
            "error", "counts_exceed_items",
            f"declared counts require {need} body items (workflows={w}, "
            f"entities={e}, scenes={s}) but only {len(body)} follow the "
            f"header+meta; expected total >= {need + 2}, actual {len(data)}",
            "items[0]",
        )
        raise _fail(".clap header counts exceed available items",
                    [*diagnostics, d])

    workflows_raw = body[0:w]
    entities_raw = body[w:w + e]
    scenes_raw = body[w + e:w + e + s]
    segments_raw = body[w + e + s:]

    declared_segments = header.declared_segments
    if declared_segments is not None and declared_segments != len(segments_raw):
        d = Diagnostic(
            "error", "segment_count_mismatch",
            f"header numberOfSegments={declared_segments} but "
            f"{len(segments_raw)} segments remain after "
            f"header+meta+{w}+{e}+{s}", "items[0].numberOfSegments",
        )
        raise _fail(".clap segment count does not match header",
                    [*diagnostics, d])

    workflows = [ClapWorkflow(r) for r in workflows_raw]
    entities = [ClapEntity(r) for r in entities_raw]
    scenes = [ClapScene(r) for r in scenes_raw]
    segments = [ClapSegment(r) for r in segments_raw]

    # --- per-segment advisory diagnostics (never fatal) -----------------------
    seg_base = 2 + w + e + s
    for idx, seg in enumerate(segments):
        seg_path = f"items[{seg_base + idx}]"
        if "endTimeInMs" not in seg.raw:
            diagnostics.append(Diagnostic(
                "warning", "segment_missing_end_time",
                f"segment {seg.id!r} has no endTimeInMs", seg_path))
        elif not seg.has_valid_end_time:
            diagnostics.append(Diagnostic(
                "warning", "segment_invalid_end_time",
                f"segment {seg.id!r} has an invalid endTimeInMs="
                f"{seg.raw.get('endTimeInMs')!r} (kept raw, not zeroed)", seg_path))
        if not seg.category_is_known and seg.raw.get("category") is not None:
            diagnostics.append(Diagnostic(
                "info", "segment_unknown_category",
                f"segment {seg.id!r} has unknown category "
                f"{seg.raw.get('category')!r} (preserved verbatim)", seg_path))

    for wf in workflows:
        if wf.raw_provider is not None and profile.normalize_provider(
                wf.raw_provider) != str(wf.raw_provider).strip().lower():
            # provider was repaired (e.g. COMFUI -> comfyui)
            diagnostics.append(Diagnostic(
                "info", "provider_normalized",
                f"workflow provider {wf.raw_provider!r} normalized to "
                f"{wf.provider!r} (raw preserved)", "workflow.provider"))

    return ClapDocument(
        raw_items=data,
        header=header,
        meta=meta,
        workflows=workflows,
        entities=entities,
        scenes=scenes,
        segments=segments,
        diagnostics=diagnostics,
    )


# -------------------------------------------------------------------- write


def _dump_yaml_bytes(items: list[dict[str, Any]]) -> bytes:
    """Deterministic YAML for the top-level array.

    ``sort_keys=False`` preserves each mapping's original key order (round-trip
    fidelity); export dicts are built in a fixed order so output stays stable.
    A wide line width keeps long locators unwrapped for stable diffs.
    """
    text = yaml.safe_dump(
        items,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=4096,
    )
    return text.encode("utf-8")


def write_clap(document: ClapDocument, path: str | Path) -> list[Diagnostic]:
    """Serialize ``document.raw_items`` back to a gzip ``.clap`` file (atomic).

    Header counts are recomputed from the actual section sizes recorded on the
    document (the only silent normalization allowed) and any correction is
    returned as an ``info`` diagnostic. gzip is written with ``mtime=0`` so the
    bytes are deterministic across runs.
    """
    path = Path(path)
    diagnostics: list[Diagnostic] = []

    counts = document.actual_counts
    hdr = document.header.raw
    for key, actual in (
        ("numberOfWorkflows", counts["workflows"]),
        ("numberOfEntities", counts["entities"]),
        ("numberOfScenes", counts["scenes"]),
        ("numberOfSegments", counts["segments"]),
    ):
        if hdr.get(key) != actual:
            if key in hdr:
                diagnostics.append(Diagnostic(
                    "info", "header_count_corrected",
                    f"corrected header {key} {hdr.get(key)!r} -> {actual}",
                    f"items[0].{key}"))
            hdr[key] = actual

    payload = _dump_yaml_bytes(document.raw_items)
    compressed = gzip.compress(payload, compresslevel=9, mtime=0)
    _atomic_write_bytes(path, compressed)
    return diagnostics


# ------------------------------------------------------------------ inspect


def inspect_clap(document: ClapDocument) -> dict[str, Any]:
    """A never-writing summary of a parsed document (drives ``openclap inspect``).

    Reports header (declared counts), actual per-section counts, a meta summary,
    per-category segment histogram, entity/scene/workflow summaries, an
    ``assetUrl`` locator histogram, unknown-field statistics (count + distinct
    keys per section) and unknown category/provider values, plus all
    diagnostics.
    """
    segs = document.segments

    category_hist: Counter[str] = Counter()
    unknown_categories: Counter[str] = Counter()
    locator_hist: Counter[str] = Counter()
    for seg in segs:
        cat = seg.raw.get("category")
        canon, known = profile.classify_category(cat)
        category_hist[canon if known else (str(cat) if cat is not None else "<none>")] += 1
        if cat is not None and not known:
            unknown_categories[str(cat)] += 1
        if seg.asset_url is not None:
            locator_hist[seg.locator_kind] += 1

    entity_categories: Counter[str] = Counter()
    for ent in document.entities:
        canon, known = profile.classify_category(ent.raw_category)
        entity_categories[canon if known else str(ent.raw_category)] += 1

    unknown_providers: Counter[str] = Counter()
    for wf in document.workflows:
        rp = wf.raw_provider
        if isinstance(rp, str) and profile.normalize_provider(rp) not in (
                "comfyui",) and rp.strip().lower() not in _COMMON_PROVIDERS:
            unknown_providers[rp] += 1

    unknown_fields: dict[str, dict[str, Any]] = {}

    def _collect(section: str, views: list[Any]) -> None:
        items_with_unknown = 0
        distinct: set[str] = set()
        for v in views:
            uk = v.unknown_keys(section)
            if uk:
                items_with_unknown += 1
                distinct.update(uk)
        if items_with_unknown or distinct:
            unknown_fields[section] = {
                "items_with_unknown_keys": items_with_unknown,
                "distinct_unknown_keys": sorted(distinct),
            }

    _collect("header", [document.header])
    _collect("meta", [document.meta])
    _collect("workflow", document.workflows)
    _collect("entity", document.entities)
    _collect("scene", document.scenes)
    _collect("segment", segs)

    return {
        "header": {
            "format": document.header.format,
            "declared": {
                "workflows": document.header.declared_workflows,
                "entities": document.header.declared_entities,
                "scenes": document.header.declared_scenes,
                "segments": document.header.declared_segments,
            },
        },
        "actual_counts": document.actual_counts,
        "meta": {
            "title": document.meta.title,
            "width": document.meta.width,
            "height": document.meta.height,
            "duration_in_ms": document.meta.duration_in_ms,
            "orientation": document.meta.orientation,
        },
        "segment_categories": dict(sorted(category_hist.items())),
        "entity_categories": dict(sorted(entity_categories.items())),
        "scene_count": len(document.scenes),
        "workflow_count": len(document.workflows),
        "locator_histogram": dict(sorted(locator_hist.items())),
        "unknown_categories": dict(sorted(unknown_categories.items())),
        "unknown_providers": dict(sorted(unknown_providers.items())),
        "unknown_fields": unknown_fields,
        "diagnostics": [d.to_dict() for d in document.diagnostics],
    }


# Providers we consider "known" enough not to flag in inspect's unknown list.
_COMMON_PROVIDERS = frozenset({
    "comfyui", "replicate", "falai", "huggingface", "openai", "stabilityai",
    "elevenlabs", "anthropic", "aitube", "modelslab", "civitai",
})

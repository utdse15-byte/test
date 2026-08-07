"""Stable script source-span parsing for authoring/coverage projections."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .hashing import hash_file, hash_text
from .model_base import ManjuModel

SOURCE_SPAN_SCHEMA = "manju.source-span/v1"
_SCENE_RE = re.compile(r"^\s*<!--\s*manju:scene\s+([^\s]+)\s*-->\s*$")
_SPAN_RE = re.compile(r"^\s*<!--\s*manju:span\s+([^\s]+)\s*-->\s*$")
_MARKER_HINT_RE = re.compile(r"<!--\s*manju:")


class SourceSpan(ManjuModel):
    artifact_path: str
    artifact_sha256: str
    scene_id: str
    span_id: str
    text_sha256: str
    start_line_hint: int
    end_line_hint: int
    text: str


class SourceSpanError(ValueError):
    pass


def parse_source_spans(text: str, *, artifact_path: str = "story/script.md",
                       artifact_sha256: str | None = None) -> list[SourceSpan]:
    """Parse stable markers; line hints are diagnostics, IDs are identity."""
    artifact_hash = artifact_sha256 or hash_text(text)
    current_scene: str | None = None
    seen_scenes: set[str] = set()
    pending_id: str | None = None
    pending_line = 0
    rows: list[SourceSpan] = []
    seen: set[str] = set()
    lines = text.splitlines()

    def finish(end_line: int) -> None:
        nonlocal pending_id, pending_line
        if pending_id is None:
            return
        body = "\n".join(lines[pending_line:end_line]).strip()
        if not body:
            raise SourceSpanError(f"empty source span {pending_id}")
        if current_scene is None:
            raise SourceSpanError(f"span {pending_id} appears before a scene marker")
        prefix = pending_id.split("-B", 1)[0] if "-B" in pending_id else ""
        if prefix != current_scene:
            raise SourceSpanError(
                f"span {pending_id} does not belong to current scene {current_scene}"
            )
        if pending_id in seen:
            raise SourceSpanError(f"duplicate source span {pending_id}")
        seen.add(pending_id)
        rows.append(SourceSpan(
            artifact_path=artifact_path,
            artifact_sha256=artifact_hash,
            scene_id=current_scene,
            span_id=pending_id,
            text_sha256=hash_text(body),
            start_line_hint=pending_line + 1,
            end_line_hint=end_line,
            text=body,
        ))
        pending_id = None

    for index, line in enumerate(lines):
        scene_match = _SCENE_RE.match(line)
        span_match = _SPAN_RE.match(line)
        if _MARKER_HINT_RE.search(line) and not (scene_match or span_match):
            raise SourceSpanError(f"malformed source marker on line {index + 1}")
        if scene_match:
            finish(index)
            current_scene = scene_match.group(1)
            if current_scene in seen_scenes:
                raise SourceSpanError(f"duplicate scene marker {current_scene}")
            seen_scenes.add(current_scene)
            continue
        if span_match:
            finish(index)
            pending_id = span_match.group(1)
            pending_line = index + 1
    finish(len(lines))
    return rows


def load_source_spans(project: Any, relative_path: str = "story/script.md") -> list[SourceSpan]:
    root = Path(project.root).resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise SourceSpanError("source span path escapes project root")
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    rows = parse_source_spans(
        text,
        artifact_path=path.relative_to(root).as_posix(),
        artifact_sha256=hash_file(path),
    )
    known_scenes = set(project.scene_contract_ids())
    unknown = sorted({row.scene_id for row in rows if row.scene_id not in known_scenes})
    if unknown:
        raise SourceSpanError("source spans reference unknown scene contract(s): "
                              + ", ".join(unknown))
    return rows


def source_span_map(project: Any, relative_path: str = "story/script.md") -> dict[str, SourceSpan]:
    return {span.span_id: span for span in load_source_spans(project, relative_path)}


__all__ = [
    "SOURCE_SPAN_SCHEMA", "SourceSpan", "SourceSpanError", "parse_source_spans",
    "load_source_spans", "source_span_map",
]

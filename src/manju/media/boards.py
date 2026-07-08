"""Multi-image storyboards + keyframe scaffolding (goal item 12, round U).

Three jobs, all deterministic and content-addressed the same way ``frames.py``
caches single frames:

- :func:`make_board` composes N cell images into a labelled 2×2 (4-panel) or
  3×3 (9-panel) grid via ffmpeg ``xstack`` — uniform cell scaling with pad (no
  distortion), each cell captioned with a drawtext label, empty cells filled
  black. It is the raw compositor.
- :func:`scene_board` pulls each shot of a scene's *best frame* (a declared ref
  image, else a mid-frame extracted from the shot's newest take via
  ``frames.extract_frame``) and composes them in shot order. The result is
  cached under ``.manju/frames`` keyed by the input file hashes + grid, exactly
  like ``frames.py`` — a re-request with unchanged inputs is free.
- :func:`breakdown_action` splits an action description into N beats at clause
  boundaries (Chinese 、然后/接着/再/并且 · English then/and then), and
  :func:`scaffold_keyframes` writes those beats into a shot's ``keyframes`` as
  suggested start/mid/end frames — truth-is-text via the normal spec write path,
  respecting locks.

Discipline mirrors ``frames.py``: the board cache lives under ``.manju/frames``
(derived, disposable, §3 — ``manju gc`` wipes it, everything rebuilds), sources
are read-only, and entries are addressed by their inputs' *content hashes* so
identical inputs share one composite.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.container import Project
from ..core.hashing import cache_key, hash_file, short_hash
from .card import find_font
from .ffmpeg import MediaError, atomic_output, default_log, run_ffmpeg
from .frames import extract_frame, frames_cache_dir
from .probe import probe_duration_ms

# grid (panel count) -> (cols, rows). 4 (2×2) and 9 (3×3) are the storyboard
# grids; 2 (2×1, side-by-side) was added in round X (agent XB) for the
# consistency-QC pair board (qc/agent_review.py) — two frames compared directly,
# no blank filler cells.
GRID_DIMS: dict[int, tuple[int, int]] = {2: (2, 1), 4: (2, 2), 9: (3, 3)}
_DEFAULT_CELL = (480, 270)  # 16:9-ish default when no project aspect is known
_JPEG_EXTS = (".jpg", ".jpeg")


# ------------------------------------------------------------------ compositor


def grid_dims(grid: int) -> tuple[int, int]:
    """``(cols, rows)`` for a panel count. Raises for anything but 2, 4 or 9."""
    try:
        return GRID_DIMS[int(grid)]
    except (KeyError, ValueError, TypeError):
        raise MediaError(f"grid must be 2 (2×1), 4 (2×2) or 9 (3×3), got {grid!r}") from None


def _even(n: int) -> int:
    n = int(round(n))
    return n if n % 2 == 0 else n + 1


def _escape_drawtext(text: str) -> str:
    """Make a short label safe inside ``drawtext=text='…'``. Labels are shot
    ids / indices / short scene names; problematic glyphs are escaped or swapped
    for a look-alike so a stray colon/quote can never break the filtergraph."""
    s = str(text)
    s = s.replace("\\", "\\\\").replace(":", "\\:").replace("%", "\\%")
    s = s.replace("'", "’")  # straight apostrophe → curly (avoids quote break)
    s = s.replace("\n", " ").replace("\r", " ")
    return s


def _escape_path(path: Path | str) -> str:
    """Escape a font path for a filtergraph option value (same rules as card.py)."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _label_drawtext(label: str | None, font: Path | None, cw: int, ch: int) -> str:
    if not label:
        return ""
    fontsize = max(12, min(cw, ch) // 11)
    opts = [
        f"text='{_escape_drawtext(label)}'",
        "x=8", "y=8", "fontcolor=white",
        f"fontsize={fontsize}",
        "box=1", "boxcolor=black@0.55", "boxborderw=6",
    ]
    if font is not None:
        opts.append(f"fontfile={_escape_path(font)}")
    return "drawtext=" + ":".join(opts)


def make_board(
    images: list[Path | None],
    grid: int,
    out: Path,
    *,
    labels: list[str] | None = None,
    cell: tuple[int, int] = _DEFAULT_CELL,
    bg: str = "black",
    font: Path | None = None,
    log=None,
) -> Path:
    """Compose a labelled 2×2 (grid=4) or 3×3 (grid=9) storyboard grid.

    ``images`` are the cell pictures in reading order (row-major); an entry of
    ``None`` — or a slot beyond ``len(images)`` — becomes a black cell, so a
    scene with fewer shots than panels still yields a full grid. Each cell is
    scaled to a uniform ``cell`` size with ``force_original_aspect_ratio=decrease``
    + ``pad`` (letterboxed, never distorted), captioned with ``labels[i]`` (shot
    id / index) via drawtext, then tiled with ffmpeg ``xstack``. Output format
    follows ``out``'s suffix (.jpg/.jpeg → mjpeg q3, else e.g. .png). The write
    is atomic (temp + os.replace)."""
    cols, rows = grid_dims(grid)
    cells = cols * rows
    cw, ch = _even(cell[0]), _even(cell[1])
    if font is None:
        font = find_font()

    ff_inputs: list[str] = []
    cell_filters: list[str] = []
    for i in range(cells):
        img = images[i] if (images and i < len(images)) else None
        label = labels[i] if (labels and i < len(labels)) else None
        if img is not None:
            ff_inputs += ["-i", str(img)]
        else:
            ff_inputs += ["-f", "lavfi", "-i", f"color=c={bg}:s={cw}x{ch}"]
        chain = (
            f"[{i}:v]scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
            f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:color={bg},setsar=1"
        )
        dt = _label_drawtext(label, font, cw, ch)
        if dt:
            chain += "," + dt
        chain += f"[c{i}]"
        cell_filters.append(chain)

    xin = "".join(f"[c{i}]" for i in range(cells))
    graph = (
        ";".join(cell_filters)
        + f";{xin}xstack=inputs={cells}:grid={cols}x{rows}:fill={bg}:shortest=1[out]"
    )

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    is_jpg = out.suffix.lower() in _JPEG_EXTS
    with atomic_output(out) as tmp:
        args = ff_inputs + ["-filter_complex", graph, "-map", "[out]", "-frames:v", "1"]
        if is_jpg:
            args += ["-q:v", "3"]
        args += [str(tmp)]
        run_ffmpeg(args, log=log)
        if not Path(tmp).is_file() or Path(tmp).stat().st_size == 0:
            raise MediaError(f"board composition produced no output for {out.name}")
    return out


# ------------------------------------------------------------------ scene board


def _shot_cell_dims(project: Project) -> tuple[int, int]:
    """Panel size matching the project's aspect (round-Q: cards/boards use the
    real aspect, not a hardcoded 9:16). Base width 480, height from the ratio."""
    try:
        cfg = project.load_config()
        w, h = int(cfg.width), int(cfg.height)
        if w <= 0 or h <= 0:
            raise ValueError
    except Exception:
        return _DEFAULT_CELL
    cw = 480
    ch = _even(round(cw * h / w))
    return cw, ch


def board_cell_dims(project: Project) -> tuple[int, int]:
    """Public alias of :func:`_shot_cell_dims` (round X, agent XB) — the
    consistency-QC contact sheets (qc/agent_review.py) reuse the same
    project-aspect cell sizing as scene boards, without reaching into a
    name-mangled private helper."""
    return _shot_cell_dims(project)


def _best_frame(project: Project, shot, bible: dict) -> Path | None:
    """The single frame that best represents a shot: a DECLARED reference image
    (params/shot/bible tier — never the generic media/refs fallback, which would
    misrepresent the shot), else a mid-frame pulled from the shot's newest take
    via ``frames.extract_frame``. ``None`` when the shot has neither."""
    from ..providers.refs import resolve_refs

    refset = resolve_refs(project, shot, bible)
    for it in refset.declared_items("image"):
        if it.path is not None and it.exists:
            return it.path

    takes = [t for t in project.takes(shot.id) if t.media_path is not None]
    if not takes:
        return None
    media = takes[-1].media_path  # newest take (append-only numbering)
    try:
        rel = project.relpath(media)
    except Exception:
        return None
    dur = probe_duration_ms(media) or 0
    at_ms = dur // 2 if dur else 0
    try:
        return extract_frame(project, rel, at_ms)
    except MediaError:
        return None


def _board_key(frames: list[Path | None], labels: list[str], grid: int,
               cw: int, ch: int) -> str:
    """Content-addressed key = input file hashes + labels + grid + cell dims —
    stable across calls (frames.py contract). A missing frame hashes as a fixed
    ``blank`` sentinel so it stays deterministic."""
    parts: list[list[str]] = []
    for i in range(grid):
        f = frames[i] if i < len(frames) else None
        lab = labels[i] if i < len(labels) else ""
        fh = hash_file(f) if (f is not None and Path(f).is_file()) else "blank"
        parts.append([fh, lab or ""])
    return short_hash(cache_key("board_v1", grid, cw, ch, parts))


def scene_board(project: Project, scene_id: str, grid: int = 4, *, log=None) -> Path:
    """Compose a storyboard grid of ``scene_id``'s shots, in shot order.

    Each cell is the shot's best frame (declared ref image, else a mid-frame of
    its newest take) captioned with the shot id. Up to ``grid`` shots fill the
    panels; extra shots are dropped, missing shots become black cells. The
    composite is cached under ``.manju/frames`` keyed by the input frames' content
    hashes + grid, so an unchanged scene returns the SAME file without
    re-rendering (frames.py caching contract). Raises :class:`MediaError` when
    the scene has no shots."""
    cols, rows = grid_dims(grid)
    cells = cols * rows
    bible = project.load_bible()

    scene_shots: list[str] = []
    for sid in project.shot_ids():
        try:
            shot = project.load_shot(sid)
        except Exception:
            continue
        if shot.scene == scene_id:
            scene_shots.append(sid)
    if not scene_shots:
        raise MediaError(
            f"scene '{scene_id}' has no shots — nothing to board "
            "(check each shot's `scene:` field)"
        )

    chosen = scene_shots[:cells]
    frames: list[Path | None] = []
    labels: list[str] = []
    for sid in chosen:
        shot = project.load_shot(sid)
        frames.append(_best_frame(project, shot, bible))
        labels.append(sid)

    cw, ch = _shot_cell_dims(project)
    key = _board_key(frames, labels, grid, cw, ch)
    cache = frames_cache_dir(project.root)
    dest = cache / f"board_{grid}_{key}.jpg"
    if dest.exists():
        return dest  # content-addressed hit: no re-render

    cache.mkdir(parents=True, exist_ok=True)
    make_board(frames, grid, dest, labels=labels, cell=(cw, ch),
               log=log or default_log(project.root, "boards"))
    return dest


# ------------------------------------------------------- action breakdown (D)

# Clause separators, longest-first so multi-char connectives win over their
# substrings. English first (word-boundary, case-insensitive), then Chinese
# connectives, then punctuation. Deterministic and pure — no state, no locale.
_EN_SEPS = (r"\band\s+then\b", r"\bthen\b", r"\band\b")
_CJK_SEPS = ("接下来", "然后", "接着", "并且", "而后", "随后", "再")
_PUNCT_SEPS = ("、", "，", ",", ";", "；", "。", "！", "!", "？", "?", "\n", "\r")
_SENTINEL = "\x00"
_QI_CHENG_ZHUAN_HE = "起承转合"


def _split_clauses(text: str) -> list[str]:
    """Split at clause boundaries — robust to pure-CJK no-space text (the CJK
    connectives and punctuation are matched literally, not on whitespace)."""
    s = (text or "").strip()
    if not s:
        return []
    for pat in _EN_SEPS:
        s = re.sub(pat, _SENTINEL, s, flags=re.IGNORECASE)
    for tok in _CJK_SEPS:
        s = s.replace(tok, _SENTINEL)
    for tok in _PUNCT_SEPS:
        s = s.replace(tok, _SENTINEL)
    return [p.strip() for p in s.split(_SENTINEL) if p.strip()]


def _bisect(text: str) -> tuple[str, str]:
    """Split one clause near its middle — at the whitespace nearest the midpoint
    for spaced (English) text, else at the character midpoint (CJK)."""
    s = text.strip()
    if len(s) < 2:
        return s, ""
    mid = len(s) // 2
    spaces = [i for i, ch in enumerate(s) if ch == " "]
    if spaces:
        pos = min(spaces, key=lambda i: (abs(i - mid), i))
        left, right = s[:pos].strip(), s[pos:].strip()
        if left and right:
            return left, right
    return s[:mid].strip(), s[mid:].strip()


def _regroup(clauses: list[str], n: int) -> list[str]:
    """Merge more-than-n clauses into exactly n contiguous groups, as evenly as
    possible, joined with the Chinese enumeration comma (deterministic)."""
    k = len(clauses)
    base, extra = divmod(k, n)
    groups: list[str] = []
    idx = 0
    for g in range(n):
        size = base + (1 if g < extra else 0)
        groups.append("、".join(clauses[idx:idx + size]))
        idx += size
    return groups


def _pad(clauses: list[str], n: int) -> list[str]:
    """Grow fewer-than-n clauses to n. First split the longest splittable beat
    repeatedly; if nothing can be split further, pad with 起/承/转/合-labelled
    duplicates — all deterministic (first-max wins, stable seed)."""
    beats = list(clauses)
    guard = 0
    while len(beats) < n and guard < 10_000:
        guard += 1
        idx = max(range(len(beats)), key=lambda i: (len(beats[i]), -i))
        if len(beats[idx].strip()) < 2:
            break
        left, right = _bisect(beats[idx])
        if not left or not right:
            break
        beats[idx:idx + 1] = [left, right]
    while len(beats) < n:
        j = len(beats)
        seed = beats[j % len(beats)] if beats else ""
        label = _QI_CHENG_ZHUAN_HE[j % 4]
        beats.append(f"{label}·{seed}" if seed else label)
    return beats


def breakdown_action(text: str, n: int) -> list[str]:
    """Deterministically split an action description into exactly ``n`` beats.

    Splits at clause boundaries (Chinese 、然后/接着/再/并且/随后/而后/接下来,
    English then/and then/and, plus sentence punctuation), robust to pure-CJK
    text with no spaces. When there are more clauses than ``n`` they are merged
    into ``n`` contiguous groups; fewer, and the longest clause is split (or,
    when nothing can be split, padded with 起/承/转/合 beat labels). Empty text
    yields ``n`` 起承转合 placeholder beats. Same input ⇒ same output, always."""
    n = max(1, int(n))
    clauses = _split_clauses(text or "")
    if not clauses:
        out: list[str] = []
        for i in range(n):
            label = _QI_CHENG_ZHUAN_HE[i % 4]
            out.append(label if i < 4 else f"{label}{i // 4 + 1}")
        return out
    if len(clauses) == n:
        return clauses
    if len(clauses) > n:
        return _regroup(clauses, n)
    return _pad(clauses, n)


# ------------------------------------------------------- keyframe scaffolding


class KeyframeScaffoldError(RuntimeError):
    """`manju board keyframes --scaffold` refused to write (e.g. a sealed
    ``keyframes`` field, §5). Carries a one-line 中文 reason."""


def beats_to_keyframes(beats: list[str]) -> list[dict]:
    """Map ordered beats to keyframe dicts: the first is the START frame, the
    last the END frame, the rest MID frames — the shape the first/last-frame
    task and the storyboard read. Prompt-only (image left for a human to fill)."""
    kfs: list[dict] = []
    last = len(beats) - 1
    for i, beat in enumerate(beats):
        if i == 0:
            pos = "start"
        elif i == last:
            pos = "end"
        else:
            pos = "mid"
        kfs.append({"position": pos, "prompt": beat})
    return kfs


def scaffold_keyframes(project: Project, shot_id: str, beats: list[str]) -> list[dict]:
    """Write ``beats`` into ``shots/<id>.yaml`` as a ``keyframes`` sequence —
    truth-is-text via ``update_shot_raw`` (the normal spec write path), so a
    human reviews the diff. Refuses (§5) when ``keyframes`` is sealed: a lock on
    ``keyframes`` (or any ``keyframes.*`` path) raises
    :class:`KeyframeScaffoldError` rather than overturning a sealed field."""
    raw = project.load_shot_raw(shot_id)  # raises ProjectError if the shot is missing
    locked = raw.get("locked") or {}
    if isinstance(locked, list):
        locked = {str(p): "" for p in locked}
    if isinstance(locked, dict) and any(
        str(p) == "keyframes" or str(p).startswith("keyframes")
        for p in locked
    ):
        raise KeyframeScaffoldError(
            f"{shot_id}: keyframes 已锁定(§5),--scaffold 拒绝覆盖已封存字段;"
            "先 `manju unlock` 再重试"
        )
    kfs = beats_to_keyframes(beats)
    project.update_shot_raw(shot_id, lambda d: d.__setitem__("keyframes", kfs))
    return kfs

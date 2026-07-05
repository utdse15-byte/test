"""Ken Burns provider (§8.4) — a network-independent local fallback.

Turns a reference image into a slow push/pan clip via ``manju.media.kenburns``.
Together with :mod:`caption_card` it guarantees a project can always produce a
picture offline, so the fallback chain never dead-ends on a network outage.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..core.container import TakeInfo
from .base import FailureKind, GenerationRequest, Provider, ProviderFailure

_IMAGE_EXTS = (".png", ".jpg", ".jpeg")


class KenburnsProvider(Provider):
    id = "ffmpeg_kenburns"
    kind = "local"

    def generate(self, req: GenerationRequest) -> list[TakeInfo]:
        image, image_source = self._resolve_image(req)
        if image is None:
            raise ProviderFailure(
                FailureKind.invalid,
                f"no reference image for shot {req.shot.id} "
                "(checked params.image, bible ref_image, media/refs)",
            )

        from ..media.kenburns import kenburns  # lazy: media may not exist yet

        config = req.project.load_config()
        n = max(1, req.candidates)
        image_ref = _ref_str(req.project, image)
        tmp = Path(tempfile.mkdtemp(prefix=f"kenburns_{req.shot.id}_"))
        takes: list[TakeInfo] = []
        try:
            for i in range(n):
                zoom_to = round(1.10 + 0.03 * i, 4)  # vary motion per candidate
                dest = tmp / f"cand_{i:02d}.mp4"
                out = kenburns(
                    image,
                    dest,
                    width=config.width,
                    height=config.height,
                    fps=config.fps,
                    duration_ms=req.duration_ms,
                    zoom_from=1.0,
                    zoom_to=zoom_to,
                    log=req.params.get("_log"),
                )
                takes.append(
                    self._register(
                        req,
                        Path(out),
                        params={
                            "image": image_ref,
                            # lineage of the reference choice (§4.2): the
                            # build graph advises when a shot fell back to a
                            # generic refs_dir image (showcase finding)
                            "image_source": image_source,
                            "zoom_from": 1.0,
                            "zoom_to": zoom_to,
                            "duration_ms": req.duration_ms,
                        },
                    )
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return takes

    # -- reference image resolution order (a) -> (b) -> (c) -----------------

    def _resolve_image(self, req: GenerationRequest) -> tuple[Path | None, str]:
        """Returns (image, source) where source names the resolution tier:
        'params' | 'bible' | 'refs_dir_fallback' — the last one means a
        generic project image was used, worth an advisory downstream."""
        project = req.project

        # (a) explicit param
        explicit = req.params.get("image")
        if explicit:
            cand = _as_path(project, explicit)
            if cand and cand.exists():
                return cand, "params"

        # (b) bible ref_image: first character(s), then scene
        bible = req.bible or {}
        keys: list[str] = list(req.shot.characters)
        if req.shot.scene:
            keys.append(req.shot.scene)
        for key in keys:
            entry = bible.get(key)
            if isinstance(entry, dict) and entry.get("ref_image"):
                cand = _as_path(project, entry["ref_image"])
                if cand and cand.exists():
                    return cand, "bible"

        # (c) first image under media/refs, sorted by name
        if project.refs_dir.exists():
            imgs = sorted(
                (
                    p
                    for p in project.refs_dir.glob("*")
                    if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
                ),
                key=lambda p: p.name,
            )
            if imgs:
                return imgs[0], "refs_dir_fallback"

        return None, "none"


def _as_path(project, value) -> Path | None:
    """Resolve a value that may be absolute or project-relative."""
    p = Path(value)
    if p.is_absolute():
        return p
    try:
        return project.resolve(value)
    except Exception:
        return None


def _ref_str(project, path: Path) -> str:
    try:
        return project.relpath(path)
    except Exception:
        return str(path)

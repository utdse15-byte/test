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


class KenburnsProvider(Provider):
    id = "ffmpeg_kenburns"
    kind = "local"

    def generate(self, req: GenerationRequest) -> list[TakeInfo]:
        refset = req.refset()
        image = refset.primary_image
        image_source = refset.primary_image_source
        if image is None:
            # A ref refused by the intake guard (link / non-regular / escaping
            # file in media/refs) leaves NO usable image: name the refusal here
            # or the only signal the owner gets is "no reference image".
            blocked = [it.blocked_reason for it in refset.image_items()
                       if it.blocked_reason]
            detail = ("; ".join(blocked)) if blocked else ""
            raise ProviderFailure(
                FailureKind.invalid,
                f"no reference image for shot {req.shot.id} "
                "(checked params.image, bible ref_image, media/refs)"
                + (f" — {detail}" if detail else ""),
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
                            # how the reference was delivered (goal item 7) —
                            # kenburns consumes the frame as a local path
                            "ref_delivery": {
                                "image_mode": "path",
                                "images": [{"ref": image_ref, "tier": image_source,
                                            "delivered_as": "path"}],
                            },
                            "zoom_from": 1.0,
                            "zoom_to": zoom_to,
                            "duration_ms": req.duration_ms,
                        },
                    )
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return takes


def _ref_str(project, path: Path) -> str:
    try:
        return project.relpath(path)
    except Exception:
        return str(path)

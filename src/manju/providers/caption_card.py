"""Caption-card provider (§8.4) — the guaranteed last-resort provider.

Renderer preference (M3 / §2.5 P1): the HTML card (headless Chromium,
HyperFrames-style deterministic HTML→MP4) when available, the zero-dependency
drawtext card as the always-there fallback. Both need no reference image and
no network, so the fallback chain always ends here and a project can always
produce *something* watchable offline (§8.4). The renderer that actually ran
is recorded in the take's params — lineage stays honest (§4.2).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..core.container import TakeInfo
from .base import GenerationRequest, Provider


class CaptionCardProvider(Provider):
    id = "caption_card"
    kind = "local"

    def generate(self, req: GenerationRequest) -> list[TakeInfo]:
        text = req.shot.dialogue.text or req.shot.action.main or req.shot.id
        config = req.project.load_config()
        want = req.params.get("renderer", "auto")  # auto | html | drawtext
        log = req.params.get("_log")

        tmp = Path(tempfile.mkdtemp(prefix=f"card_{req.shot.id}_"))
        try:
            dest = tmp / "card.mp4"
            renderer = "drawtext"
            out: Path | None = None
            if want in ("auto", "html"):
                try:
                    from ..media.html_card import html_card_video

                    out = html_card_video(
                        text, dest, width=config.width, height=config.height,
                        fps=config.fps, duration_ms=req.duration_ms, log=log,
                    )
                    renderer = "html"
                except Exception:
                    if want == "html":
                        raise  # explicitly requested -> surface the failure
                    out = None  # adapter wall: fall through to drawtext
            if out is None:
                from ..media.card import caption_card  # zero-dependency fallback

                out = caption_card(
                    text, dest, width=config.width, height=config.height,
                    fps=config.fps, duration_ms=req.duration_ms, log=log,
                )
            take = self._register(
                req,
                Path(out),
                params={"text": text, "duration_ms": req.duration_ms,
                        "renderer": renderer},
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return [take]

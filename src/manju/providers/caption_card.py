"""Caption-card provider (§8.4) — the guaranteed last-resort provider.

Renders the shot's text onto a plain card via ``manju.media.card``. It needs no
reference image and no network, so the fallback chain always ends here and a
project can always produce *something* watchable offline (§8.4).
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

        from ..media.card import caption_card  # lazy: media may not exist yet

        config = req.project.load_config()
        tmp = Path(tempfile.mkdtemp(prefix=f"card_{req.shot.id}_"))
        try:
            dest = tmp / "card.mp4"
            out = caption_card(
                text,
                dest,
                width=config.width,
                height=config.height,
                fps=config.fps,
                duration_ms=req.duration_ms,
                log=req.params.get("_log"),
            )
            take = self._register(
                req,
                Path(out),
                params={"text": text, "duration_ms": req.duration_ms},
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return [take]

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
                except Exception as exc:
                    if want == "html":
                        raise  # explicitly requested -> surface the failure
                    # F13: in AUTO mode we silently degrade to drawtext. Record
                    # the WHY as a best-effort level="info" DEGRADATION (the
                    # documented "a card skipped" shape — "why did this shot
                    # become a drawtext card?" answerable from the record alone)
                    # so a persistently-broken html_card is visible in `manju
                    # failures` instead of every card downgrading with no
                    # diagnostic. The renderer that ACTUALLY ran is still
                    # recorded in params below, so lineage stays honest either
                    # way; this only adds the missing reason.
                    self._record_html_degradation(req, exc)
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

    def _record_html_degradation(self, req: GenerationRequest, exc: Exception) -> None:
        """Best-effort advisory (F13): the preferred HTML renderer failed and the
        card fell back to drawtext. Recorded at ``level="info"`` (a DEGRADATION,
        not an error — never counted against the build, never in the status
        nudge) so a persistently-broken renderer is diagnosable. NEVER raises: a
        diagnostic must not break the always-there fallback floor (§8.4), so any
        failure recording it is swallowed and drawtext still runs."""
        try:
            from ..core.failures import Failure, record_failure

            record_failure(
                req.project,
                Failure(
                    step="generate", subject=req.shot.id,
                    cause=f"{self.id}: HTML 卡片渲染失败,已回退 drawtext(§2.5)",
                    evidence=" ".join(str(exc).split())[:400],
                    hint="若每张卡片都回退:检查 headless Chromium(CHROME_BIN/PATH/"
                         "PLAYWRIGHT_BROWSERS_PATH);drawtext 兜底仍能正常出片",
                    level="info", actor="engine",
                    detail={"provider": self.id, "renderer": "html",
                            "fell_back_to": "drawtext"},
                ),
            )
        except Exception:
            pass

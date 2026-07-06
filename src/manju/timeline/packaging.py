"""Pure content-addressing for packaging intro/outro card assets (round-N).

The intro/outro card MP4 is content-addressed: its project-relative path is a
pure function of ``(card model dump + width + height + fps)``. Same spec → same
path (reuse, never re-render); edited text → new path (append-only, §3). There
is NO filesystem access here, so the compiler stays a pure function of specs
(§6) — it computes the path from the hash without touching disk, and the build
graph renders whatever asset is missing before the render pass.
"""

from __future__ import annotations

from ..core.hashing import hash_value, short_hash
from ..core.models import PackagingCard

# Derived, content-addressed, append-only (mirrors media/gen semantics but for
# engine-authored cards; gitignored via the project scaffold's .gitignore).
PACKAGING_ASSET_DIR = "media/generated/_packaging"


def packaging_card_hash(card: PackagingCard, width: int, height: int, fps: int) -> str:
    """Canonical hash of everything that shapes the rendered card asset."""
    return hash_value(
        {"card": card.model_dump(), "width": width, "height": height, "fps": fps}
    )


def packaging_card_relpath(
    kind: str, card: PackagingCard, width: int, height: int, fps: int
) -> str:
    """Project-relative path of the content-addressed intro/outro card MP4.

    ``kind`` ("intro"/"outro") is the filename prefix; the 10-char hash tail is
    the content identity, so the same spec always resolves to the same file.
    """
    tail = short_hash(packaging_card_hash(card, width, height, fps), 10)
    return f"{PACKAGING_ASSET_DIR}/{kind}_{tail}.mp4"

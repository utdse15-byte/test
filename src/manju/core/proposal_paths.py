"""Shared atomic proposal-path helpers for CLI and local application services."""

from __future__ import annotations

import os
import re
from pathlib import Path


def slugify_proposal_title(title: str, maxlen: int = 40) -> str:
    """Return a deterministic, CJK-preserving filesystem slug."""
    chars = [char if char.isalnum() else "_" for char in title.strip().lower()]
    slug = re.sub(r"_+", "_", "".join(chars)).strip("_")
    if len(slug) > maxlen:
        slug = slug[:maxlen].strip("_")
    return slug or "proposal"


def next_proposal_number(proposals_dir: Path) -> int:
    """Return the next number hint from existing proposal filenames."""
    highest = 0
    if proposals_dir.exists():
        for path in proposals_dir.glob("*.md"):
            match = re.match(r"(\d+)", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def claim_proposal_path(proposals_dir: Path, slug: str) -> tuple[int, Path]:
    """Atomically claim a unique ``NNNN_<slug>.md`` proposal path.

    Directory scanning is only a starting hint. The exclusive create loop is
    the authority under concurrent CLI/GUI/agent processes.
    """
    proposals_dir.mkdir(parents=True, exist_ok=True)
    number = next_proposal_number(proposals_dir)
    while True:
        path = proposals_dir / f"{number:04d}_{slug}.md"
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            number += 1
            continue
        os.close(fd)
        return number, path


__all__ = [
    "slugify_proposal_title",
    "next_proposal_number",
    "claim_proposal_path",
]

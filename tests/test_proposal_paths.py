from __future__ import annotations

import multiprocessing
from pathlib import Path

from manju.core.proposal_paths import (
    claim_proposal_path,
    next_proposal_number,
    slugify_proposal_title,
)


def _claim_worker(directory: str, title: str) -> None:
    claim_proposal_path(Path(directory), slugify_proposal_title(title))


def test_slugify_preserves_cjk_and_falls_back_for_empty_titles():
    assert slugify_proposal_title("  Rain / 雨  ") == "rain_雨"
    assert slugify_proposal_title("!!!") == "proposal"
    assert len(slugify_proposal_title("a" * 100)) == 40


def test_claim_uses_existing_number_hint_and_exclusive_create(tmp_path):
    proposals = tmp_path / "proposals"
    proposals.mkdir()
    (proposals / "0007_existing.md").write_text("", encoding="utf-8")
    assert next_proposal_number(proposals) == 8
    number, path = claim_proposal_path(proposals, "same")
    assert number == 8
    assert path.name == "0008_same.md"


def test_concurrent_claims_get_distinct_numbers(tmp_path):
    proposals = tmp_path / "proposals"
    ctx = multiprocessing.get_context("spawn")
    processes = [
        ctx.Process(target=_claim_worker, args=(str(proposals), "同一个标题"))
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    names = sorted(path.name for path in proposals.glob("*.md"))
    assert [name[:4] for name in names] == ["0001", "0002", "0003", "0004"]

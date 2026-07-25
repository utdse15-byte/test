"""Every scaffolded file must be byte-identical on every platform.

`Path.write_text` uses ``newline=None``, so on WINDOWS it translates each "\\n"
into CRLF. A project scaffolded there would therefore carry different bytes —
and different content hashes — than the same project scaffolded anywhere else,
which is exactly the class of drift `sorted(..., key=as_posix)` and
`atomic_write_text`'s explicit ``newline="\\n"`` exist to prevent.

The scaffolds were the exception: packaging.yaml, the five bible files, the
story templates, .gitignore, and every preset seed went through the
newline-translating path. This pins the outcome (bytes on disk) rather than the
mechanism, so it holds however the writing is refactored — and it is the one
Windows invariant reachable from a Linux test, since the assertion is "no CRLF",
which a CRLF-writing platform would fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.core.container import Project

CR = b"\r"


def _all_text_files(root: Path) -> list[Path]:
    keep = {".yaml", ".yml", ".md", ".json", ".txt", ".jsonl"}
    out = [p for p in root.rglob("*")
           if p.is_file() and (p.suffix.lower() in keep or p.name == ".gitignore")]
    assert out, "scaffold produced no text files at all"
    return out


def test_a_fresh_project_has_no_cr_anywhere(tmp_path: Path) -> None:
    root = tmp_path / "lf.manju"
    Project.create(root, name="lf")
    offenders = [p.relative_to(root).as_posix()
                 for p in _all_text_files(root) if CR in p.read_bytes()]
    assert offenders == [], f"CR found in scaffolded files: {offenders}"


@pytest.mark.parametrize("rel", [
    "timeline/packaging.yaml", "timeline/rules.yaml", "project.yaml",
    "bible/characters.yaml", "bible/scenes.yaml", "bible/props.yaml",
    "bible/style.yaml", "bible/voices.yaml",
    "story/brief.md", "story/outline.md", "story/script.md", ".gitignore",
])
def test_each_named_scaffold_is_lf_only(tmp_path: Path, rel: str) -> None:
    root = tmp_path / "named.manju"
    Project.create(root, name="named")
    data = (root / rel).read_bytes()
    assert data, f"{rel} is empty"
    assert CR not in data, f"{rel} contains CR"


def test_preset_seeds_are_lf_only(tmp_path: Path) -> None:
    """Preset scaffolds replace/extend the default story files — same rule."""
    from manju.presets import apply_preset, load_preset

    root = tmp_path / "preset.manju"
    Project.create(root, name="preset")
    project = Project(root)
    spec = load_preset("vertical_ai_video")
    apply_preset(project, spec)
    offenders = [p.relative_to(root).as_posix()
                 for p in _all_text_files(root) if CR in p.read_bytes()]
    assert offenders == [], f"CR found after preset seeding: {offenders}"

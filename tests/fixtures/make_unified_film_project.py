"""Zero-network v5 rehearsal fixture; no media Provider is imported or called."""

from __future__ import annotations

from pathlib import Path

from manju.core.authoring import SceneContractV2, ShotContract, ScreenIntent
from manju.core.container import Project
from manju.core.creative import CreativeCharter, TruthRef
from manju.core.hashing import hash_file
from manju.core.models import ShotIndex, ShotSpec
from manju.core.source_spans import parse_source_spans
from manju.core.yamlio import write_yaml


def make_unified_film_project(root: Path) -> Project:
    project = Project.create(root)
    (project.root / "story" / "brief.md").write_text("A choice has a cost.\n", encoding="utf-8")
    (project.root / "story" / "ending.md").write_text("She leaves the coin behind.\n", encoding="utf-8")
    script = (
        "<!-- manju:scene SC001 -->\n"
        "<!-- manju:span SC001-B001 -->\n"
        "She hears the bell.\n"
        "<!-- manju:span SC001-B002 -->\n"
        "She chooses not to ask.\n"
    )
    (project.root / "story" / "script.md").write_text(script, encoding="utf-8")
    charter = CreativeCharter(
        brief_ref=TruthRef(path="story/brief.md", sha256=hash_file(project.root / "story" / "brief.md")),
        ending_ref=TruthRef(path="story/ending.md", sha256=hash_file(project.root / "story" / "ending.md")),
        commitments={"theme_questions": ["Can a choice remain free when the future is known?"]},
        audiovisual={"ambiguity_to_preserve": ["The bell may be a warning or coincidence."],
                     "motifs": [{"id": "BELL", "starts_as": "sound", "transforms_into": "choice"}]},
    )
    write_yaml(project.creative_path, charter.model_dump(exclude_none=True))
    project.save_scene_contract(SceneContractV2(
        id="SC001", purpose="a choice lands", proof_scene=True,
        direction={"experience": {"beats": [
            {"id": "XP_SC001_01", "role": "orientation", "viewer_task": "hear the space", "required": True},
            {"id": "XP_SC001_02", "role": "aftermath", "viewer_task": "read the choice", "required": True},
        ]}},
    ))
    project.save_shot(ShotSpec(
        id="S001", scene_id="SC001", source_span_refs=["script:SC001-B001"],
        action={"main": "listen"},
        contract=ShotContract(screen=ScreenIntent(
            role="orientation", experience_beat_refs=["XP_SC001_01"],
            primary_carrier="sound", justification="establish the bell in space",
        )),
    ))
    project.save_shot(ShotSpec(
        id="S002", scene_id="SC001", source_span_refs=["script:SC001-B002"],
        action={"main": "leave the coin"},
        contract=ShotContract(screen=ScreenIntent(
            role="aftermath", experience_beat_refs=["XP_SC001_02"],
            primary_carrier="performance", justification="let the decision breathe",
        )),
    ))
    project.save_index(ShotIndex(order=["S001", "S002"]))
    return project

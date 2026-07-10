"""WP2/WP3 run-evidence red tests (e1..e3).

A produced final must be self-describing enough to AUDIT: its ``.key.json``
sidecar should prove the bytes on disk (``output_sha256``), expose the exact
inputs that produced the content key (``inputs`` — re-hashable back to
``final_key``, not a parallel account), and carry a ``run_id`` that also lands
on the build event in events.jsonl (so a final correlates to its run record).

EXPECT RED against current code (the sidecar carries only final_key/target/
created_at). They go green under the pre-approved render.py sidecar extension +
graph.py run_id threading. Real ffmpeg media, so the file is skipped without
ffmpeg/ffprobe.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.events import tail_events
from manju.core.hashing import cache_key, hash_file
from tests.fixtures.make_sample import make_sample_project

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A tiny real project built to final once (module-scoped: e1..e3 all read
    the same freshly minted final + its sidecar)."""
    from manju.build.graph import run_build

    root = make_sample_project(
        tmp_path_factory.mktemp("dr01_evidence") / "样片", shots=2, clip_seconds=1.0,
    )
    project = Project(root)
    result = run_build(project, target="final")
    assert result.ok, result.errors
    return project


def _newest_final(project: Project) -> Path:
    finals = sorted(project.final_dir.glob("final_v*.mp4"))
    assert finals, "run_build produced no final"
    return finals[-1]


def _sidecar(project: Project) -> dict:
    final = _newest_final(project)
    sc = final.with_suffix(".key.json")
    assert sc.exists(), "final is missing its .key.json sidecar"
    return json.loads(sc.read_text(encoding="utf-8"))


def _payload_from_inputs(inputs: dict) -> dict:
    """Reverse the evidence-friendly ``inputs`` breakdown back into the exact
    payload dict ``final_content_key`` hashes (a pure key rename — cache_key
    sorts keys, so order is irrelevant)."""
    payload = {
        "timeline": inputs["timeline"],
        "segments": inputs["segment_keys"],
        "ass": inputs["ass_sha256"],
        "audio": inputs["audio"],
        "encoding": inputs["encoding"],
        "target": inputs["target"],
    }
    if "overlay_images" in inputs:
        payload["overlay_images"] = inputs["overlay_images"]
    if "look" in inputs:
        payload["look"] = inputs["look"]
    return payload


# --------------------------------------------------------------------- e1


def test_dr01_e1_sidecar_records_output_sha256(built):
    """The sidecar's ``output_sha256`` equals hash_file of the final mp4."""
    data = _sidecar(built)
    assert "output_sha256" in data, "sidecar does not record output_sha256"
    assert data["output_sha256"] == hash_file(_newest_final(built))


# --------------------------------------------------------------------- e2


def test_dr01_e2_sidecar_inputs_reconstruct_final_key(built):
    """The sidecar exposes the key components AND the breakdown re-hashes back
    to final_key (the real key input, not a parallel account)."""
    data = _sidecar(built)
    assert "inputs" in data, "sidecar does not record the key inputs breakdown"
    inputs = data["inputs"]
    for field in ("timeline", "segment_keys", "ass_sha256",
                  "audio", "encoding", "target"):
        assert field in inputs, f"inputs missing {field!r}"
    assert isinstance(inputs["segment_keys"], list)
    assert inputs["target"] == data["target"]

    payload = _payload_from_inputs(inputs)
    assert cache_key(payload) == data["final_key"], (
        "the sidecar breakdown does not reproduce final_key"
    )


# --------------------------------------------------------------------- e3


def test_dr01_e3_run_id_correlates_final_to_run(built):
    """A run_id lives on the sidecar AND on the build event in events.jsonl,
    so a final can be tied back to its run record."""
    data = _sidecar(built)
    assert data.get("run_id"), "sidecar does not carry a run_id"

    build_events = [e for e in tail_events(built.root, n=200)
                    if e.get("action") == "build"]
    assert build_events, "no build event recorded"
    run_ids = {e.get("detail", {}).get("run_id") for e in build_events}
    assert data["run_id"] in run_ids, (
        f"sidecar run_id {data['run_id']!r} not found on any build event {run_ids!r}"
    )

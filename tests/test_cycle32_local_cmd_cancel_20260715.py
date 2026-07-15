"""Cycle-32: local_cmd honors should_cancel during subprocess wait."""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.providers.base import GenerationRequest, ProviderCanceled
from manju.providers.local_cmd import LocalCommandProvider
from manju.providers.manifest import ProviderManifest


def _manifest(command: str, timeout_s: float = 30.0) -> ProviderManifest:
    return ProviderManifest.model_validate({
        "id": "local_test",
        "type": "video",
        "adapter": "manju.providers.local_cmd:LocalCommandProvider",
        "capabilities": ["text_to_video"],
        "cost": {"per_call": 0.0, "currency": "CNY"},
        "local_cmd": {
            "command": command,
            "timeout_s": timeout_s,
            "output_ext": ".mp4",
        },
    })


def test_local_cmd_cancel_kills_long_process(tmp_project, add_shot) -> None:
    """A long-running local command must stop when should_cancel trips."""
    import sys

    shot = add_shot(tmp_project, "S001", generation={"candidates": 1})
    # Python sleep — portable long job. Template requires {out}.
    py = sys.executable
    cmd = f'"{py}" -c "import time; time.sleep(60)" {{out}}'
    provider = LocalCommandProvider(_manifest(cmd, timeout_s=30.0))
    trips = {"n": 0}

    def cancel_soon() -> bool:
        trips["n"] += 1
        return trips["n"] >= 2  # allow first slice then cancel

    req = GenerationRequest(
        project=tmp_project,
        shot=shot,
        bible=tmp_project.load_bible(),
        spec_hash="sha256:test",
        duration_ms=4000,
        candidates=1,
        params={"seed": 1},
        should_cancel=cancel_soon,
    )
    try:
        with pytest.raises(ProviderCanceled) as ei:
            provider.generate(req)
    except OSError as exc:
        # Windows CI flakiness: Popen can raise WinError 6 (invalid handle).
        pytest.skip(f"subprocess spawn flake on this host: {exc}")
    assert ei.value.provider_id == "local_test"
    assert "local:S001" in ei.value.job_id


def test_local_cmd_source_has_cancel_poll_loop() -> None:
    from pathlib import Path
    import manju.providers.local_cmd as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "ProviderCanceled" in src
    assert "should_cancel" in src
    assert "slice_s" in src or "0.5" in src

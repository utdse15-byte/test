"""Provider authoring evidence is traceable advice, never capability truth."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from manju.build.doctor import run_doctor
from manju.cli import app
from manju.core.yamlio import write_yaml
from manju.providers.catalog import descriptor_for_manifest, provider_profile_digest
from manju.providers.manifest import (
    ProviderManifest,
    authoring_evidence_status,
    current_authoring_evidence,
)


runner = CliRunner()


@pytest.fixture
def providers_dir(tmp_path, monkeypatch):
    root = tmp_path / "providers"
    root.mkdir()
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(root))
    return root


def _manifest(**overrides):
    data = {
        "id": "evidence_test",
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["text_to_video"],
        "auth": {"key_env": "EVIDENCE_TEST_KEY"},
        "submit": {
            "url": "https://example.invalid/generate",
            "body_template": {"prompt": "{prompt}"},
            "job_id_path": "$.id",
        },
        "poll": {
            "url": "https://example.invalid/jobs/{job_id}",
            "status_path": "$.status",
            "status_map": {"done": "succeeded"},
        },
    }
    data.update(overrides)
    return data


def _active(status: str = "active") -> dict:
    return {
        "status": status,
        "verified_on": "2026-08-05",
        "verification_kind": "both",
        "source_refs": ["https://docs.example.invalid/model", "owner-test:take_03"],
        "model_label": "model-r3",
        "prompt_semantics": {
            "negative_prompt": "owner test found a separate negative field",
            "time_instruction": "unknown",
        },
        "owner_findings": ["Short imperative camera clauses were interpreted literally."],
        "known_failure_modes": ["Long scene prose can obscure the endpoint."],
    }


def test_missing_evidence_remains_unknown():
    manifest = ProviderManifest.model_validate(_manifest())
    assert manifest.authoring is None
    assert current_authoring_evidence(manifest) is None
    assert authoring_evidence_status(manifest) == {
        "status": "unknown",
        "current": False,
        "traceable": False,
        "verified_on": None,
        "verification_kind": None,
        "source_refs": [],
        "model_label": None,
    }


@pytest.mark.parametrize("status", ["active", "provisional"])
@pytest.mark.parametrize(
    "missing",
    ["verified_on", "verification_kind", "source_refs"],
)
def test_current_evidence_requires_traceability(status: str, missing: str):
    evidence = _active(status)
    evidence[missing] = [] if missing == "source_refs" else None
    with pytest.raises(ValidationError, match=missing):
        ProviderManifest.model_validate(_manifest(authoring=evidence))


@pytest.mark.parametrize("status", ["stale", "archived", "unknown"])
def test_non_current_evidence_never_drives_current_advice(status: str):
    evidence = _active(status)
    manifest = ProviderManifest.model_validate(_manifest(authoring=evidence))
    assert current_authoring_evidence(manifest) is None
    projection = authoring_evidence_status(manifest)
    assert projection["status"] == status
    assert projection["current"] is False


@pytest.mark.parametrize("status", ["active", "provisional"])
def test_traceable_current_evidence_is_available(status: str):
    manifest = ProviderManifest.model_validate(_manifest(authoring=_active(status)))
    current = current_authoring_evidence(manifest)
    assert current is manifest.authoring
    projection = authoring_evidence_status(manifest)
    assert projection["current"] is True
    assert projection["traceable"] is True


def test_authoring_evidence_does_not_change_machine_profile_identity():
    without = ProviderManifest.model_validate(_manifest())
    with_evidence = ProviderManifest.model_validate(
        _manifest(authoring=_active("active"))
    )
    plain_digest = provider_profile_digest(
        without.id,
        "text_to_video",
        descriptor=descriptor_for_manifest(without),
    )
    evidence_digest = provider_profile_digest(
        with_evidence.id,
        "text_to_video",
        descriptor=descriptor_for_manifest(with_evidence),
    )
    assert evidence_digest == plain_digest


@pytest.mark.parametrize("duplicate", ["capabilities", "limits", "refs", "cost", "adapter"])
def test_authoring_cannot_duplicate_manifest_owned_facts(duplicate: str):
    evidence = _active()
    evidence[duplicate] = {"invented": True}
    with pytest.raises(ValidationError, match=duplicate):
        ProviderManifest.model_validate(_manifest(authoring=evidence))


def test_show_check_and_doctor_report_evidence_status(
    providers_dir, monkeypatch
):
    monkeypatch.setenv("EVIDENCE_TEST_KEY", "present")
    write_yaml(
        providers_dir / "evidence_test" / "provider.yaml",
        _manifest(authoring=_active("provisional")),
    )

    shown = runner.invoke(app, ["providers", "show", "evidence_test", "--json"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["authoring"]["status"] == "provisional"

    checked = runner.invoke(app, ["providers", "check", "evidence_test", "--json"])
    assert checked.exit_code == 0, checked.output
    checked_doc = json.loads(checked.output)
    assert checked_doc["authoring_evidence"]["status"] == "provisional"
    assert checked_doc["authoring_evidence"]["current"] is True

    doctor = run_doctor(None)
    row = next(c for c in doctor["checks"]
               if c["name"] == "provider_authoring:evidence_test")
    assert row["ok"] is True
    assert "provisional" in row["detail"]
    assert "current" in row["detail"]


def test_show_projects_absent_authoring_as_unknown(providers_dir):
    write_yaml(providers_dir / "evidence_test" / "provider.yaml", _manifest())
    shown = runner.invoke(app, ["providers", "show", "evidence_test", "--json"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["authoring"] == {"status": "unknown"}


@pytest.mark.parametrize("authoring", [None, {"owner_findings": ["unverified note"]}])
def test_show_projects_implicit_status_as_unknown(providers_dir, authoring):
    write_yaml(
        providers_dir / "evidence_test" / "provider.yaml",
        _manifest(authoring=authoring),
    )
    shown = runner.invoke(app, ["providers", "show", "evidence_test", "--json"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["authoring"]["status"] == "unknown"

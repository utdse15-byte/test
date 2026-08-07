"""Third-round residual regressions; every test is offline."""

from __future__ import annotations

import json

import pytest

from manju.core.yamlio import write_yaml
from manju.core.spec import spec_payload
from manju.core.models import TakeSidecar
from manju.providers import submission as S
from manju.providers.base import GenerationRequest, ProviderFailure
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.manifest import ProviderManifest
from manju.providers.refbudget import ROLE_PROP, ROLE_SCENE, classify_role
from manju.providers.refs import plan_reference_delivery, resolve_refs
from manju.qc.expectations import compile_expectations
from manju.qc.agent_review import _issue_shot_packet
from manju.runtime.state import RuntimeState


def _manifest(**overrides) -> ProviderManifest:
    data = {
        "id": "wave3_cloud",
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["text_to_video"],
        "auth": {"key_env": "WAVE3_KEY"},
        "submit": {
            "url": "https://api.example.com/v1/videos",
            "body_template": {"prompt": "{prompt}", "seed": "{seed}"},
            "job_id_path": "$.data.task_id",
        },
        "poll": {
            "url": "https://api.example.com/v1/videos/{job_id}",
            "status_path": "$.data.status",
            "status_map": {"DONE": "succeeded", "RUNNING": "running"},
            "result_url_path": "$.data.video_url",
        },
        "limits": {"max_concurrent": 1, "rate_limit_per_min": 60},
        "submission": {"idempotency": {"mode": "header", "field": "Idempotency-Key"}},
    }
    data.update(overrides)
    return ProviderManifest.model_validate(data)


def _request(project) -> GenerationRequest:
    shot = project.load_shot("S001")
    return GenerationRequest(
        project=project,
        shot=shot,
        bible=project.load_bible(),
        spec_hash="sha256:wave3",
        duration_ms=3000,
        candidates=1,
        params={"seed": 7},
    )


class _RecordingTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, dict(headers), body))
        return self.responses.pop(0)


def _response(status: int, payload) -> HttpResponse:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return HttpResponse(status, {}, body)


def test_submit_url_change_moves_submission_identity(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    first = GenericCloudProvider(_manifest())._build_identity(_request(tmp_project))[1]
    changed = _manifest(submit={
        "url": "https://api.example.com/v2/videos",
        "body_template": {"prompt": "{prompt}", "seed": "{seed}"},
        "job_id_path": "$.data.task_id",
    })
    second = GenericCloudProvider(changed)._build_identity(_request(tmp_project))[1]
    assert first != second


def _identity_digest(project, manifest: ProviderManifest) -> str:
    return GenericCloudProvider(manifest)._build_identity(_request(project))[1]


def test_api_version_header_change_moves_submission_identity(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    submit = {
        "url": "https://api.example.com/v1/videos",
        "body_template": {"prompt": "{prompt}", "seed": "{seed}"},
        "job_id_path": "$.data.task_id",
        "extra_headers": {"X-API-Version": "2026-08-01"},
        "identity_headers": ["X-API-Version"],
    }
    first = _identity_digest(tmp_project, _manifest(submit=submit))
    submit["extra_headers"]["X-API-Version"] = "2026-08-02"
    assert _identity_digest(tmp_project, _manifest(submit=submit)) != first


def test_job_id_path_change_moves_submission_identity(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    changed = _manifest(submit={
        "url": "https://api.example.com/v1/videos",
        "body_template": {"prompt": "{prompt}", "seed": "{seed}"},
        "job_id_path": "$.job.id",
    })
    assert _identity_digest(tmp_project, changed) != _identity_digest(
        tmp_project, _manifest()
    )


def test_poll_url_change_moves_submission_identity(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    changed = _manifest(poll={
        "url": "https://poll.example.com/v2/jobs/{job_id}",
        "status_path": "$.data.status",
        "status_map": {"DONE": "succeeded", "RUNNING": "running"},
        "result_url_path": "$.data.video_url",
    })
    assert _identity_digest(tmp_project, changed) != _identity_digest(
        tmp_project, _manifest()
    )


def test_poll_status_map_change_moves_submission_identity(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    changed = _manifest(poll={
        "url": "https://api.example.com/v1/videos/{job_id}",
        "status_path": "$.data.status",
        "status_map": {"DONE": "failed", "RUNNING": "running"},
        "result_url_path": "$.data.video_url",
    })
    assert _identity_digest(tmp_project, changed) != _identity_digest(
        tmp_project, _manifest()
    )


def test_result_path_change_moves_submission_identity(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    changed = _manifest(poll={
        "url": "https://api.example.com/v1/videos/{job_id}",
        "status_path": "$.data.status",
        "status_map": {"DONE": "succeeded", "RUNNING": "running"},
        "result_url_path": "$.result.url",
    })
    assert _identity_digest(tmp_project, changed) != _identity_digest(
        tmp_project, _manifest()
    )


def test_idempotency_mode_change_moves_submission_identity(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    changed = _manifest(submission={"idempotency": {"mode": "none"}})
    assert _identity_digest(tmp_project, changed) != _identity_digest(
        tmp_project, _manifest()
    )


def test_idempotency_field_change_moves_submission_identity(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    changed = _manifest(submission={
        "idempotency": {"mode": "header", "field": "X-Request-Key"},
    })
    assert _identity_digest(tmp_project, changed) != _identity_digest(
        tmp_project, _manifest()
    )


def test_secret_value_rotation_does_not_move_semantic_identity(
    tmp_project, add_shot, monkeypatch,
):
    add_shot(tmp_project, "S001")
    monkeypatch.setenv("WAVE3_KEY", "first-secret-value")
    first = _identity_digest(tmp_project, _manifest())
    monkeypatch.setenv("WAVE3_KEY", "second-secret-value")
    assert _identity_digest(tmp_project, _manifest()) == first


def test_secret_value_rotation_is_not_persisted(
    tmp_project, add_shot, monkeypatch,
):
    secret = "never-persist-this-secret"
    monkeypatch.setenv("WAVE3_KEY", secret)
    add_shot(tmp_project, "S001")
    transport = _RecordingTransport([_response(503, b"unavailable")])
    provider = GenericCloudProvider(
        _manifest(), transport=transport, sleep_fn=lambda _seconds: None
    )
    with pytest.raises(ProviderFailure):
        provider.generate(_request(tmp_project))
    with RuntimeState(tmp_project.root) as state:
        [row] = state.submissions(shot="S001", provider="wave3_cloud")
    evidence = (tmp_project.root / "events.jsonl").read_text(encoding="utf-8")
    assert secret not in row["execution_profile_json"]
    assert secret not in evidence


def test_execution_profile_mismatch_blocks_redispatch_before_transport(
    tmp_project, add_shot, monkeypatch,
):
    monkeypatch.setenv("WAVE3_KEY", "secret-one")
    add_shot(tmp_project, "S001")
    first_transport = _RecordingTransport([_response(503, b"unavailable")])
    first = GenericCloudProvider(
        _manifest(), transport=first_transport, sleep_fn=lambda _seconds: None
    )
    with pytest.raises(ProviderFailure):
        first.generate(_request(tmp_project))

    changed = _manifest(submit={
        "url": "https://api.example.com/v2/videos",
        "body_template": {"prompt": "{prompt}", "seed": "{seed}"},
        "job_id_path": "$.data.task_id",
    })
    second_transport = _RecordingTransport([_response(503, b"must not be sent")])
    second = GenericCloudProvider(
        changed, transport=second_transport, sleep_fn=lambda _seconds: None
    )
    with pytest.raises(ProviderFailure) as raised:
        second.generate(_request(tmp_project))
    assert raised.value.detail["code"] == "submission_execution_profile_mismatch"
    assert second_transport.requests == []


def test_legacy_ambiguous_submission_never_redispatches_after_idempotency_is_added(
    tmp_project, add_shot, monkeypatch,
):
    monkeypatch.setenv("WAVE3_KEY", "secret")
    add_shot(tmp_project, "S001")
    current = _manifest()
    digest = _identity_digest(tmp_project, current)
    with RuntimeState(tmp_project.root) as state:
        state.open_intent(
            provider="wave3_cloud",
            shot="S001",
            submission_id="sub_legacy",
            request_digest=digest,
            state=S.OUTCOME_UNKNOWN,
        )
    transport = _RecordingTransport([_response(200, {"must": "not run"})])
    provider = GenericCloudProvider(
        current, transport=transport, sleep_fn=lambda _seconds: None
    )
    with pytest.raises(ProviderFailure) as raised:
        provider.generate(_request(tmp_project))
    assert raised.value.detail["code"] == "submission_execution_profile_unknown"
    assert transport.requests == []


def test_original_non_idempotent_submit_cannot_be_authorized_by_current_manifest(
    tmp_project, add_shot, monkeypatch,
):
    monkeypatch.setenv("WAVE3_KEY", "secret")
    add_shot(tmp_project, "S001")
    original = _manifest(submission={"idempotency": {"mode": "none"}})
    first_transport = _RecordingTransport([_response(503, b"unavailable")])
    first = GenericCloudProvider(
        original, transport=first_transport, sleep_fn=lambda _seconds: None
    )
    with pytest.raises(ProviderFailure):
        first.generate(_request(tmp_project))

    second_transport = _RecordingTransport([_response(200, {"must": "not run"})])
    second = GenericCloudProvider(
        _manifest(), transport=second_transport, sleep_fn=lambda _seconds: None
    )
    with pytest.raises(ProviderFailure) as raised:
        second.generate(_request(tmp_project))
    assert raised.value.detail["code"] == "submission_execution_profile_mismatch"
    assert second_transport.requests == []


def test_recovery_uses_stored_profile_not_live_changed_poll_schema(
    tmp_project, add_shot, monkeypatch,
):
    monkeypatch.setenv("WAVE3_KEY", "secret")
    add_shot(tmp_project, "S001")
    first_transport = _RecordingTransport([
        _response(200, {"data": {"task_id": "job_1"}}),
        _response(200, {"data": {"status": "RUNNING"}}),
    ])
    first = GenericCloudProvider(
        _manifest(), transport=first_transport, sleep_fn=lambda _seconds: None,
        timeout_s=0, max_retries=0,
    )
    with pytest.raises(ProviderFailure):
        first.generate(_request(tmp_project))
    with RuntimeState(tmp_project.root) as state:
        [row] = state.submissions(shot="S001", provider="wave3_cloud")
    assert row["state"] == S.ADMITTED

    changed = _manifest(poll={
        "url": "https://api.example.com/v1/videos/{job_id}",
        "status_path": "$.state",
        "status_map": {"DONE": "failed", "RUNNING": "succeeded"},
        "result_url_path": "$.result.url",
    })
    second_transport = _RecordingTransport([_response(200, {"must": "not run"})])
    second = GenericCloudProvider(
        changed, transport=second_transport, sleep_fn=lambda _seconds: None
    )
    with pytest.raises(ProviderFailure) as raised:
        second.generate(_request(tmp_project))
    assert raised.value.detail["code"] == "submission_execution_profile_mismatch"
    assert second_transport.requests == []


def test_character_bible_ref_infers_subject_scope(tmp_project, add_shot):
    ref = tmp_project.resolve("media/refs/linxia.png")
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(b"image")
    bible = tmp_project.load_bible()
    bible["linxia"]["ref_image"] = "media/refs/linxia.png"
    shot = add_shot(tmp_project, "S001")

    [item] = [item for item in resolve_refs(tmp_project, shot, bible).items
              if item.tier == "bible"]
    assert item.subject_ref == "character:linxia"
    assert item.declared_transfer is False


def test_prop_and_scene_bible_refs_infer_scopes(tmp_project, add_shot):
    for name in ("scene", "prop"):
        path = tmp_project.resolve(f"media/refs/{name}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode("ascii"))
    write_yaml(tmp_project.root / "bible" / "scenes.yaml", {
        "convenience_store": {"ref_image": "media/refs/scene.png"},
    })
    write_yaml(tmp_project.root / "bible" / "props.yaml", {
        "coin": {"ref_image": "media/refs/prop.png"},
    })
    shot = add_shot(tmp_project, "S001", props=["coin"])
    refs = resolve_refs(tmp_project, shot, tmp_project.load_bible())
    assert {item.subject_ref for item in refs.items if item.tier == "bible"} == {
        "scene:convenience_store", "prop:coin",
    }


def test_explicit_subject_ref_overrides_inferred_scope(tmp_project, add_shot):
    path = tmp_project.resolve("media/refs/hero.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"hero")
    bible = tmp_project.load_bible()
    bible["linxia"]["ref_image"] = {
        "ref": "media/refs/hero.png",
        "subject_ref": "prop:portrait",
    }
    shot = add_shot(tmp_project, "S001")
    [item] = [item for item in resolve_refs(tmp_project, shot, bible).items
              if item.tier == "bible"]
    assert item.subject_ref == "prop:portrait"
    assert item.declared_transfer is True


def test_same_blob_preserves_two_inferred_bindings_but_one_physical_upload(
    tmp_project, add_shot,
):
    path = tmp_project.resolve("media/refs/group.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"group")
    write_yaml(tmp_project.root / "bible" / "characters.yaml", {
        "A": {"ref_image": "media/refs/group.png"},
        "B": {"ref_image": "media/refs/group.png"},
    })
    shot = add_shot(tmp_project, "S001", characters=["A", "B"])
    bible = tmp_project.load_bible()
    refs = resolve_refs(tmp_project, shot, bible)
    bindings = [item for item in refs.items if item.tier == "bible"]
    assert [item.subject_ref for item in bindings] == ["character:A", "character:B"]
    assert len(refs.images) == 1
    assert [row["subject_ref"] for row in refs.lineage["images"]] == [
        "character:A", "character:B",
    ]


def test_runtime_scope_equals_spec_v5_scope(tmp_project, add_shot):
    path = tmp_project.resolve("media/refs/store.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"store")
    write_yaml(tmp_project.root / "bible" / "scenes.yaml", {
        "convenience_store": {"ref_image": "media/refs/store.png"},
    })
    shot = add_shot(tmp_project, "S001")
    bible = tmp_project.load_bible()
    runtime_scopes = [
        item.subject_ref for item in resolve_refs(tmp_project, shot, bible).items
        if item.tier == "bible"
    ]
    spec_scopes = [
        row["subject_scope"]
        for row in spec_payload(
            shot, bible, version=5, project_root=tmp_project.root
        )["references"]
        if row["tier"] == "bible"
    ]
    assert runtime_scopes == spec_scopes == ["scene:convenience_store"]


def test_budget_uses_canonical_scene_and_prop_scopes(tmp_project, add_shot):
    for name in ("scene", "prop"):
        path = tmp_project.resolve(f"media/refs/{name}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode("ascii"))
    write_yaml(tmp_project.root / "bible" / "scenes.yaml", {
        "convenience_store": {
            "name": "Store",
            "ref_image": {"ref": "media/refs/scene.png"},
        },
    })
    write_yaml(tmp_project.root / "bible" / "props.yaml", {
        "coin": {"name": "Coin", "ref_image": {"ref": "media/refs/prop.png"}},
    })
    shot = add_shot(tmp_project, "S001", props=["coin"])
    bible = tmp_project.load_bible()
    items = resolve_refs(tmp_project, shot, bible).items
    roles = {item.subject_ref: classify_role(item, shot, bible) for item in items}
    assert roles["scene:convenience_store"] == ROLE_SCENE
    assert roles["prop:coin"] == ROLE_PROP


def test_budget_classifies_dict_primary_and_secondary_character_refs(
    tmp_project, add_shot,
):
    for name in ("a", "b"):
        path = tmp_project.resolve(f"media/refs/{name}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode("ascii"))
    write_yaml(tmp_project.root / "bible" / "characters.yaml", {
        "A": {"ref_image": {"ref": "media/refs/a.png"}},
        "B": {"ref_image": {"ref": "media/refs/b.png"}},
    })
    shot = add_shot(tmp_project, "S001", characters=["A", "B"])
    bible = tmp_project.load_bible()
    roles = {
        item.subject_ref: classify_role(item, shot, bible)
        for item in resolve_refs(tmp_project, shot, bible).items
    }
    assert roles == {
        "character:A": "character_primary",
        "character:B": "character",
    }


def test_same_blob_multiple_logical_roles_keeps_budget_evidence_and_uploads_once(
    tmp_project, add_shot,
):
    path = tmp_project.resolve("media/refs/shared.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"shared")
    write_yaml(tmp_project.root / "bible" / "characters.yaml", {
        "A": {"ref_image": "media/refs/shared.png"},
    })
    write_yaml(tmp_project.root / "bible" / "scenes.yaml", {
        "convenience_store": {"ref_image": "media/refs/shared.png"},
    })
    shot = add_shot(tmp_project, "S001", characters=["A"])
    bible = tmp_project.load_bible()
    refs = resolve_refs(tmp_project, shot, bible)
    manifest = _manifest(
        refs={
            "image_mode": "base64_field",
            "field": "$.images",
            "max_images": 1,
        },
        limits={
            "max_concurrent": 1,
            "rate_limit_per_min": 60,
            "max_ref_images": 1,
        },
    )
    plan = plan_reference_delivery(
        GenericCloudProvider(manifest), refs, shot, bible
    )
    assert len(plan.selected_refset().images) == 1
    assert [row["role"] for row in plan.budget_lineage["selected"]] == [
        "character_primary", "scene",
    ]
    assert [row["subject_ref"] for row in plan.budget_lineage["selected"]] == [
        "character:A", "scene:convenience_store",
    ]


def test_scoped_opening_facts_compile_as_distinct_v3_expectations(
    tmp_project, add_shot,
):
    statement = "remains motionless"
    add_shot(tmp_project, "S001", contract={
        "opening": [
            {"subject_ref": "character:A", "statement": statement},
            {"subject_ref": "character:B", "statement": statement},
        ],
        "acceptance": {"action_required": False},
    })
    compiled = compile_expectations(tmp_project, "S001")
    rows = [row for row in compiled["expectations"] if row["statement"] == statement]
    assert compiled["schema"] == "manju.qc.expectations/v3"
    assert {row["subject_scope"] for row in rows} == {
        "character:A", "character:B",
    }
    assert len({row["id"] for row in rows}) == 2


def test_unscoped_contract_remains_expectations_v2(tmp_project, add_shot):
    add_shot(tmp_project, "S001", contract={
        "opening": ["remains motionless"],
        "acceptance": {"action_required": False},
    })
    compiled = compile_expectations(tmp_project, "S001")
    assert compiled["schema"] == "manju.qc.expectations/v2"
    assert all("subject_scope" not in row for row in compiled["expectations"])


def test_character_and_prop_same_id_have_distinct_expectations(
    tmp_project, add_shot,
):
    statement = "remains motionless"
    add_shot(tmp_project, "S001", contract={
        "opening": [
            {"subject_ref": "character:A", "statement": statement},
            {"subject_ref": "prop:A", "statement": statement},
        ],
        "acceptance": {"action_required": False},
    })
    compiled = compile_expectations(tmp_project, "S001")
    rows = [row for row in compiled["expectations"] if row["statement"] == statement]
    assert {row["subject_scope"] for row in rows} == {"character:A", "prop:A"}
    assert len({row["id"] for row in rows}) == 2


def test_scope_change_moves_expectation_id_and_digest(tmp_project, add_shot):
    statement = "remains motionless"
    add_shot(tmp_project, "S001", contract={
        "opening": [{"subject_ref": "character:A", "statement": statement}],
        "acceptance": {"action_required": False},
    })
    first = compile_expectations(tmp_project, "S001")
    tmp_project.update_shot_raw(
        "S001",
        lambda raw: raw["contract"]["opening"][0].update(
            {"subject_ref": "character:B"}
        ),
    )
    second = compile_expectations(tmp_project, "S001")
    assert first["expectations"][0]["id"] != second["expectations"][0]["id"]
    assert first["digest"] != second["digest"]


def test_review_packet_preserves_expectation_scope(tmp_project, add_shot):
    statement = "remains motionless"
    add_shot(tmp_project, "S001", contract={
        "opening": [{"subject_ref": "character:A", "statement": statement}],
        "acceptance": {"action_required": False},
    })
    source = tmp_project.root / "offline.mp4"
    source.write_bytes(b"offline-media")
    take = tmp_project.register_take(
        "S001", source, TakeSidecar(provider="offline", spec_hash="sha256:test")
    )
    packet = _issue_shot_packet(tmp_project, "S001", take, {})
    [row] = [item for item in packet["expectations"]
             if item["statement"] == statement]
    assert row["subject_scope"] == "character:A"

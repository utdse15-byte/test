"""Emit deterministic, secret-free proof for the localhost fake provider."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from manju.core.hashing import hash_value
from manju.providers.catalog import descriptor_for_manifest
from manju.providers.generic_cloud import render_body
from manju.providers.manifest import ProviderManifest, estimate_cost
from manju.providers.preflight import check_request_compatibility
from manju.providers.submission import ProviderExecutionProfile
from manju.providers.zero_cost import (
    LOCAL_PROOF_SCHEMA,
    STRICT_ZERO_COST,
    execution_policy_snapshot,
    require_transport_allowed,
    strict_zero_cost_active,
)

REQUEST_FACTS = {
    "prompt": "synthetic local proof",
    "duration_ms": 4000,
    "duration_s": 4.0,
    "resolution": "720p",
    "width": 1280,
    "height": 720,
    "candidates": 1,
}


def proof_document(profile_path: Path) -> dict[str, object]:
    if not strict_zero_cost_active():
        raise RuntimeError(f"MANJU_EXECUTION_MODE must be {STRICT_ZERO_COST}")
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    manifest = ProviderManifest.model_validate(raw)
    problems = manifest.validate_for_generic()
    if problems:
        raise RuntimeError("invalid localhost fake profile: " + "; ".join(problems))

    submit = manifest.submit
    assert submit is not None
    require_transport_allowed(submit.url, credential_ref=manifest.auth.key_env)
    execution_profile = ProviderExecutionProfile.from_manifest(manifest)
    rendered = render_body(submit.body_template, REQUEST_FACTS)
    request_facts = {
        "provider_id": manifest.id,
        "method": submit.method.upper(),
        "endpoint": submit.url,
        "body": rendered,
        "duration_ms": REQUEST_FACTS["duration_ms"],
        "resolution": REQUEST_FACTS["resolution"],
        "candidates": REQUEST_FACTS["candidates"],
        "simulated_cost": estimate_cost(
            manifest, REQUEST_FACTS["duration_ms"], REQUEST_FACTS["candidates"]
        ),
    }
    descriptor = descriptor_for_manifest(manifest)
    preflight = check_request_compatibility(
        descriptor,
        capability="text_to_video",
        duration_ms=REQUEST_FACTS["duration_ms"],
        width=REQUEST_FACTS["width"],
        height=REQUEST_FACTS["height"],
        params={
            "resolution": REQUEST_FACTS["resolution"],
            "candidates": REQUEST_FACTS["candidates"],
        },
        body_placeholders=submit.body_template,
    )
    return {
        "schema": LOCAL_PROOF_SCHEMA,
        "policy": execution_policy_snapshot(),
        "provider": {
            "id": manifest.id,
            "credential_ref": manifest.auth.key_env,
            "execution_profile_digest": execution_profile.digest,
        },
        "request": {**request_facts, "digest": hash_value(request_facts)},
        "preflight": preflight,
        "transport_count": 0,
        "network_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    document = proof_document(args.profile)
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

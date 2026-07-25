"""A support bundle must keep the facts it exists to carry.

The secret-key pattern is a SUBSTRING match on field names, so every field
merely containing "key" was redacted — including `content_key` and `final_key`,
the build's own content fingerprints. Those are precisely what a bundle is for:
"why did it re-render" is answered by comparing content keys, and a bundle that
hides them cannot answer it.

Both directions are pinned here, because loosening redaction is the kind of
change that must not be able to drift: known build fingerprints survive, and
anything else keeping the word "key" (an actual `api_key`, or a name nobody has
allowlisted) is still masked.
"""

from __future__ import annotations

import pytest

from manju.core.supportbundle import REDACTED, redact_record


def _r(obj):
    return redact_record(obj, {})


# ------------------------------------------------ build fingerprints survive


@pytest.mark.parametrize("name", [
    "content_key", "final_key", "base_key", "output_key",
    "windows_collision_key", "next_step_key", "has_key",
    "submission_idempotency_key",
])
def test_build_fingerprint_fields_are_not_redacted(name: str) -> None:
    value = "sha256:812305306d05ee39a98165f67c44a6fbf750096a95a80d6c521675a179e324dd"
    assert _r({name: value}) == {name: value}


def test_a_realistic_attempt_row_keeps_its_content_key() -> None:
    row = {"role": "final", "bytes": 2029767,
           "content_key": "sha256:8123053", "sha256": "sha256:aa"}
    assert _r(row)["content_key"] == "sha256:8123053"


# --------------------------------------------------- real secrets still go


@pytest.mark.parametrize("name", [
    "api_key", "apiKey", "API_KEY", "secret_key", "access_token",
    "authorization", "x_signature", "password", "bearer_token",
    "some_unknown_key",   # not allowlisted → still masked, fail-closed
])
def test_credential_shaped_fields_are_still_redacted(name: str) -> None:
    assert _r({name: "sk-abcdefghijklmnopqrstuvwxyz0123"}) == {name: REDACTED}


def test_the_allowlist_is_exact_not_a_substring_rule() -> None:
    """`content_key` passing must not let `content_key_secret` through."""
    out = _r({"content_key_secret": "sk-abcdefghijklmnopqrstuvwxyz0123",
              "my_content_key": "sk-abcdefghijklmnopqrstuvwxyz0123"})
    assert out["content_key_secret"] == REDACTED
    assert out["my_content_key"] == REDACTED


def test_nested_and_listed_records_follow_the_same_rule() -> None:
    out = _r({"outputs": [{"content_key": "sha256:ok", "api_key": "sk-zzzzzzzzzzzzzzzzzzzzzz"}]})
    assert out["outputs"][0]["content_key"] == "sha256:ok"
    assert out["outputs"][0]["api_key"] == REDACTED

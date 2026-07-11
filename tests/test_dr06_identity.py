"""DR06 — the pure submission module: identity, digest, state machine,
disposition, event chain, idempotency, redaction (contract §7 + rulings 2/3/6).

Every test here is a pure unit test of ``providers.submission`` — no project, no
network, no DB. It is the fastest, most exhaustive tier of the DR06 suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.core.hashing import hash_value
from manju.providers import submission as S


def _identity(**over):
    base = dict(
        shot_id="S001", spec_hash="sha256:spec", provider_id="video_x",
        provider_profile_digest="sha256:profile", capability="text_to_video",
        duration_ms=4000, candidates=1, seed=7, params={"seed": 7},
        compiled_prompt="雨夜便利店,冷白灯管", ref_refs=[], first_frame=False,
        last_frame=False, project_root=None,
    )
    base.update(over)
    return S.build_submission_identity(**base)


# ------------------------------------------------------------------ submission_id


def test_submission_id_format_and_uniqueness():
    a = S.mint_submission_id()
    b = S.mint_submission_id()
    assert a.startswith("sub_") and len(a) == len("sub_") + 12
    assert a != b


def test_submission_id_is_not_the_request_digest():
    ident = _identity()
    assert S.mint_submission_id() != S.request_digest(ident)  # distinct spaces


# --------------------------------------------------------------- identity/digest


def test_identity_schema_and_included_fields():
    ident = _identity()
    assert ident["schema"] == S.SCHEMA_IDENTITY
    # every remote-result-affecting fact is present (§7.3)
    for key in ("shot_id", "spec_hash", "provider_id", "provider_profile_digest",
                "capability", "duration_ms", "candidates", "seed", "params",
                "compiled_prompt_digest", "refs", "first_frame", "last_frame"):
        assert key in ident


def test_compiled_prompt_is_digested_never_stored():
    ident = _identity(compiled_prompt="a very secret prompt text 悬疑")
    assert ident["compiled_prompt_digest"] == hash_value("a very secret prompt text 悬疑")
    # the plaintext appears nowhere in the identity document
    assert "a very secret prompt" not in str(ident)


def test_digest_is_stable_for_same_semantics():
    assert S.request_digest(_identity()) == S.request_digest(_identity())


def test_digest_moves_when_profile_digest_changes():
    """The DR04 provider_profile_digest is an identity input — a provider profile
    change (limits/refs/cost) moves the request_digest (ruling 3)."""
    a = S.request_digest(_identity(provider_profile_digest="sha256:profileA"))
    b = S.request_digest(_identity(provider_profile_digest="sha256:profileB"))
    assert a != b


@pytest.mark.parametrize("field,val", [
    ("shot_id", "S002"), ("spec_hash", "sha256:other"), ("capability", "image_to_video"),
    ("duration_ms", 5000), ("candidates", 2), ("seed", 8),
    ("first_frame", True), ("last_frame", True),
])
def test_digest_moves_on_any_semantic_field(field, val):
    assert S.request_digest(_identity()) != S.request_digest(_identity(**{field: val}))


def test_digest_moves_when_params_change():
    a = S.request_digest(_identity(params={"seed": 7, "cfg": 4.5}))
    b = S.request_digest(_identity(params={"seed": 7, "cfg": 4.6}))
    assert a != b


def test_digest_excludes_secrets_and_signed_urls_in_params():
    """§7.5: an api key param and a signed-URL query are excluded from identity —
    two requests differing ONLY in those hash identically."""
    clean = S.request_digest(_identity(params={"seed": 7}))
    with_key = S.request_digest(_identity(
        params={"seed": 7, "api_key": "k-SECRET-123", "token": "abc"}))
    assert clean == with_key  # secret-named keys never affect identity
    url_a = S.request_digest(_identity(params={"seed": 7, "img": "https://c/x.png?sig=AAA"}))
    url_b = S.request_digest(_identity(params={"seed": 7, "img": "https://c/x.png?sig=BBB"}))
    assert url_a == url_b  # signed query stripped before hashing


def test_canonical_params_bool_is_distinct_from_int():
    a = S.request_digest(_identity(params={"hd": True}))
    b = S.request_digest(_identity(params={"hd": 1}))
    assert a != b  # bool != int, explicitly guarded


def test_canonical_params_key_order_irrelevant():
    a = S.request_digest(_identity(params={"a": 1, "b": 2}))
    b = S.request_digest(_identity(params={"b": 2, "a": 1}))
    assert a == b


def test_canonical_params_path_is_project_relative(tmp_path):
    root = tmp_path / "proj"
    (root / "refs").mkdir(parents=True)
    inside = root / "refs" / "a.png"
    inside.write_bytes(b"x")
    rel = S.canonical_params({"img": inside}, project_root=root)
    assert rel["img"] == {"__t": "path", "v": "refs/a.png"}  # relative, never absolute
    # a path outside the root never leaks an absolute string
    outside = S.canonical_params({"img": tmp_path / "elsewhere.png"}, project_root=root)
    assert outside["img"]["v"] == "<external-path>"


def test_ref_fact_shape_and_delivery_order_matters():
    r1 = S.ref_fact("image", "refs/a.png", "sha256:aa")
    r2 = S.ref_fact("image", "refs/b.png", "sha256:bb")
    assert r1 == {"role": "image", "logical_id": "refs/a.png", "content_sha256": "sha256:aa"}
    fwd = S.request_digest(_identity(ref_refs=[r1, r2]))
    rev = S.request_digest(_identity(ref_refs=[r2, r1]))
    assert fwd != rev  # delivery order is semantic


def test_ref_fact_strips_signed_url_logical_id():
    r = S.ref_fact("image", "https://cdn/x.png?sig=SECRET", None)
    assert "SECRET" not in r["logical_id"]


# --------------------------------------------------------------- state machine


def test_states_and_legal_transitions_map_is_complete():
    for st in S.STATES:
        assert st in S.LEGAL_TRANSITIONS


def test_happy_path_transitions_are_legal():
    assert S.is_legal_transition(S.PREPARED, S.DISPATCHING)
    assert S.is_legal_transition(S.DISPATCHING, S.ADMITTED)
    assert S.is_legal_transition(S.ADMITTED, S.TERMINAL_SUCCESS)


def test_recovery_transitions_are_legal():
    assert S.is_legal_transition(S.OUTCOME_UNKNOWN, S.ADMITTED)       # attach
    assert S.is_legal_transition(S.OUTCOME_UNKNOWN, S.ABANDONED_BY_USER)  # abandon
    assert S.is_legal_transition(S.DISPATCHING, S.ABANDONED_BY_USER)


def test_illegal_transitions_are_rejected():
    assert not S.is_legal_transition(S.PREPARED, S.ADMITTED)          # must claim first
    assert not S.is_legal_transition(S.TERMINAL_SUCCESS, S.DISPATCHING)  # terminal is terminal
    assert not S.is_legal_transition(S.OUTCOME_UNKNOWN, S.TERMINAL_SUCCESS)  # never auto-succeed
    with pytest.raises(S.IllegalTransition):
        S.assert_transition(S.PREPARED, S.ADMITTED)


def test_unknown_state_normalizes_to_legacy_never_invented():
    assert S.normalize_state(None) == S.UNKNOWN_LEGACY
    assert S.normalize_state("WHATEVER") == S.UNKNOWN_LEGACY
    assert S.normalize_state(S.ADMITTED) == S.ADMITTED


# --------------------------------------------------------------- disposition


@pytest.mark.parametrize("status,expected", [
    # P0 WP2 flip: definite rejection is now manifest-declared, not a global
    # table. With NOTHING declared, EVERY status >= 400 is conservatively
    # OUTCOME_UNKNOWN (the request may have created a billable job); < 400 stays
    # ADMITTED (the caller's job-id parse decides).
    (400, S.OUTCOME_UNKNOWN_DISPOSITION), (401, S.OUTCOME_UNKNOWN_DISPOSITION),
    (403, S.OUTCOME_UNKNOWN_DISPOSITION), (404, S.OUTCOME_UNKNOWN_DISPOSITION),
    (409, S.OUTCOME_UNKNOWN_DISPOSITION), (422, S.OUTCOME_UNKNOWN_DISPOSITION),
    (429, S.OUTCOME_UNKNOWN_DISPOSITION),
    (500, S.OUTCOME_UNKNOWN_DISPOSITION), (502, S.OUTCOME_UNKNOWN_DISPOSITION),
    (503, S.OUTCOME_UNKNOWN_DISPOSITION), (408, S.OUTCOME_UNKNOWN_DISPOSITION),
    (200, S.ADMITTED_DISPOSITION),
])
def test_disposition_for_status_undeclared(status, expected):
    # no declared set -> nothing is DEFINITELY_REJECTED by default
    assert S.disposition_for_status(status) == expected


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429])
def test_disposition_for_status_declared_is_definite(status):
    # P0 WP2: a status the MANIFEST declares is DEFINITELY_REJECTED (fallback ok);
    # a status NOT in the declared set stays OUTCOME_UNKNOWN.
    declared = {status}
    assert S.disposition_for_status(status, declared) == S.DEFINITELY_REJECTED
    assert S.disposition_for_status(500, declared) == S.OUTCOME_UNKNOWN_DISPOSITION
    # < 400 is never a rejection even if (nonsensically) declared
    assert S.disposition_for_status(200, declared) == S.ADMITTED_DISPOSITION


def test_disposition_for_transport_error_only_dns_and_refused_are_not_dispatched():
    import socket

    assert S.disposition_for_transport_error(socket.gaierror("name")) == S.NOT_DISPATCHED
    assert S.disposition_for_transport_error(ConnectionRefusedError()) == S.NOT_DISPATCHED
    # a URLError wrapping a DNS failure is unwrapped
    import urllib.error
    wrapped = urllib.error.URLError(socket.gaierror("nodename"))
    assert S.disposition_for_transport_error(wrapped) == S.NOT_DISPATCHED
    # everything else after the send boundary is OUTCOME_UNKNOWN (conservative)
    assert S.disposition_for_transport_error(TimeoutError()) == S.OUTCOME_UNKNOWN_DISPOSITION
    assert S.disposition_for_transport_error(
        ConnectionResetError()) == S.OUTCOME_UNKNOWN_DISPOSITION
    assert S.disposition_for_transport_error(OSError("boom")) == S.OUTCOME_UNKNOWN_DISPOSITION


def test_disposition_to_state_mapping():
    assert S.disposition_to_state(S.NOT_DISPATCHED) == S.REJECTED_PRE_DISPATCH
    assert S.disposition_to_state(S.DEFINITELY_REJECTED) == S.REMOTE_REJECTED
    assert S.disposition_to_state(S.ADMITTED_DISPOSITION) == S.ADMITTED
    assert S.disposition_to_state(S.OUTCOME_UNKNOWN_DISPOSITION) == S.OUTCOME_UNKNOWN
    assert S.disposition_to_state(None) == S.OUTCOME_UNKNOWN  # conservative default


# --------------------------------------------------------------- idempotency


def test_idempotency_key_is_stable_and_secret_free():
    k1 = S.idempotency_key("video_x", "sub_abc123")
    k2 = S.idempotency_key("video_x", "sub_abc123")
    assert k1 == k2  # stable across re-dispatch of the same submission
    assert k1 == hash_value([S.IDEMPOTENCY_NAMESPACE, "video_x", "sub_abc123"]).split(":", 1)[-1]
    assert not k1.startswith("sha256:")  # bare, drops into a header value
    assert S.idempotency_key("video_x", "sub_def456") != k1  # per submission


# --------------------------------------------------------------- event chain


def _chain(n):
    """Build a valid n-link chain of submission events."""
    events = []
    prev = None
    states = [S.PREPARED, S.DISPATCHING, S.ADMITTED, S.TERMINAL_SUCCESS]
    for i in range(n):
        ev = {
            "submission_id": "sub_x", "request_digest": "sha256:d",
            "from": states[i] if i else None, "to": states[min(i, len(states) - 1)],
            "provider_id": "video_x", "remote_job_id": None,
            "reason_code": None, "event_id": f"evt_{i}", "prev_event_digest": prev,
        }
        events.append(ev)
        prev = S.submission_event_digest(ev)
    return events


def test_intact_chain_verifies():
    ok, broken = S.verify_chain(_chain(4))
    assert ok and broken is None
    assert S.verify_chain([]) == (True, None)  # empty chain is intact
    assert S.verify_chain(_chain(1)) == (True, None)


def test_tampered_event_breaks_the_chain():
    events = _chain(4)
    events[2]["to"] = "TAMPERED"  # mutate a middle event's content
    ok, broken = S.verify_chain(events)
    assert not ok
    assert broken == 3  # the link AFTER the tampered event no longer matches


def test_dropped_event_breaks_the_chain():
    events = _chain(4)
    del events[1]  # remove a link
    ok, broken = S.verify_chain(events)
    assert not ok


# --------------------------------------------------------------- redaction


def test_redact_reason_strips_signed_url_and_bounds():
    out = S.redact_reason("failed GET https://cdn/x.png?sig=SECRET&exp=9  \n  next")
    assert "SECRET" not in out
    assert "\n" not in out
    long = S.redact_reason("x" * 5000)
    assert len(long) <= 401

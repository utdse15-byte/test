"""The fake ``qc_vision`` reviewer + its disagreeing twin (AI_IDE_20A, contract
§5 Reviewer Calibration + §8 Harness: 完全离线的 deterministic fake path).

This is a TEST DOUBLE that exercises the REAL packet / verdict / intake plumbing
(``qc.agent_review.qc_brief`` → ``record_verdicts`` → ``qc.assurance``) with ZERO
network and ZERO model. Manju core never calls a VLM (contract §0/§7); this
double stands in for the "configured qc_vision provider" slot so AI_IDE_15 can
drive the multi-reviewer / calibration flows deterministically.

How it works:

* Observations come from a lookup table keyed by the INPUT MEDIA sha256 (the
  ``media.sha256`` a packet binds), falling back to a DECLARED default for any
  media not in the table. The table + default live in manifest.json — the
  committed golden corpus is the single source of truth for the pinned hashes,
  so this module hard-codes no hash.
* It emits the AI_IDE_15 §6 observation contract shape: per-expectation
  ``{expectation_id, observed ∈ present|absent|uncertain|not_evaluated}`` plus
  severity-tiered ``findings`` — exactly what ``_record_verdicts_v2`` consumes,
  so 15 can intake it unchanged. ``observe()`` additionally exposes the richer
  §6 dimension/observed/severity/allow_unknown view for calibration scoring.
* The PRIMARY profile (``qc_vision_fake``) is calibrated — it agrees with each
  case's annotated ``expected_observations``. The TWIN (``qc_vision_fake_twin``)
  carries deliberate conflicts on declared cases + a distinct ``profile_digest``,
  so 15's disagreement-rate / multi-reviewer tests have a real second opinion.

The two provider manifests load through the REAL registry via
``MANJU_PROVIDERS_DIR`` (the pattern used across the provider tests): see
:func:`install_manifests`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from manju.core.hashing import hash_value, short_hash
from manju.core.yamlio import read_yaml
from manju.qc.agent_review import LEVELS, OBSERVED_STATES, VERDICT_SCHEMA

GOLDEN_DIR = Path(__file__).resolve().parent
PROVIDERS_DIR = GOLDEN_DIR / "providers"

PROFILES = ("primary", "twin")
# profile -> the committed provider manifest id it occupies the vision slot as.
MANIFEST_IDS = {"primary": "qc_vision_fake", "twin": "qc_vision_fake_twin"}


class FakeReviewerError(RuntimeError):
    pass


def manifest_path() -> Path:
    return GOLDEN_DIR / "manifest.json"


def load_manifest() -> dict:
    return json.loads(manifest_path().read_text(encoding="utf-8"))


def _provider_yaml(profile: str) -> Path:
    return PROVIDERS_DIR / MANIFEST_IDS[profile] / "provider.yaml"


# --------------------------------------------------------------- stance model
# A "stance" is what the reviewer reports about ONE media, resolved per
# expectation by polarity:
#   present -> the observed state for a present-polarity (must_show / lock) exp
#   absent  -> the observed state for an absent-polarity (avoid) exp
#   severity-> when non-null, a finding at this level rides the verdict
#   dimension/message -> §6 provenance for observe()


def _valid_observed(x: str) -> str:
    if x not in OBSERVED_STATES:
        raise FakeReviewerError(
            f"stance observed {x!r} not one of {OBSERVED_STATES}")
    return x


def _bare(h: str | None) -> str | None:
    """Normalize a content hash to bare hex for table lookup. ``core.hashing.
    hash_file`` (and every packet's ``media.sha256``) carries a ``sha256:``
    prefix; manifest.json stores the conventional bare ``hashlib`` hexdigest (the
    form the corpus self-test verifies). Stripping the ``algo:`` prefix lets the
    two meet."""
    if h and ":" in h:
        return h.split(":", 1)[1]
    return h


class FakeVisionReviewer:
    """A deterministic, offline visual reviewer keyed by media sha256.

    ``profile`` selects the calibrated primary or the disagreeing twin. Pass a
    pre-loaded ``manifest`` dict to avoid re-reading manifest.json."""

    def __init__(self, profile: str = "primary", manifest: dict | None = None):
        if profile not in PROFILES:
            raise FakeReviewerError(f"unknown profile {profile!r}; expected {PROFILES}")
        self.profile = profile
        self.manifest_id = MANIFEST_IDS[profile]
        self._manifest = manifest or load_manifest()
        self._table = self._build_table()
        self._default = self._manifest["fake_reviewer_defaults"][profile]

    # ---- table construction -------------------------------------------------

    def _build_table(self) -> dict[str, dict]:
        """{media_sha256: stance} from every committed-image case that declares a
        reviewer stance for this profile. Keyed by the case's PINNED sha256 — so
        registering that image as a take makes its packet resolve here."""
        table: dict[str, dict] = {}
        for case in self._manifest["cases"]:
            rv = (case.get("reviewer") or {}).get(self.profile)
            sha = case.get("sha256")
            if rv is None or not sha:
                continue
            _valid_observed(rv["present"])
            _valid_observed(rv["absent"])
            table[_bare(sha)] = {
                "present": rv["present"],
                "absent": rv["absent"],
                "severity": rv.get("severity"),
                "dimension": case.get("dimension", "unspecified"),
                "message": rv.get("message", f"{case['id']} 判读"),
                "case_id": case["id"],
            }
        return table

    # ---- resolution ---------------------------------------------------------

    def stance_for(self, media_sha256: str | None) -> dict:
        """The resolved stance for a media hash — the declared table entry, or
        the DECLARED default fallback (contract §5: UNKNOWN is a first-class
        outcome, never a silent pass)."""
        key = _bare(media_sha256)
        if key and key in self._table:
            return self._table[key]
        d = self._default
        return {
            "present": d["present"], "absent": d["absent"],
            "severity": d.get("severity"),
            "dimension": d.get("dimension", "unspecified"),
            "message": d.get("message", "未登记媒体:默认判读(UNKNOWN)"),
            "case_id": None,
        }

    @property
    def profile_digest(self) -> str:
        """A deterministic digest over the profile's provider manifest + its
        resolved table/default — primary and twin differ, and any stance edit
        moves it (contract §5/§6: model/profile digest, stale detection)."""
        try:
            manifest_body = read_yaml(_provider_yaml(self.profile)) or {}
        except OSError:
            manifest_body = {"id": self.manifest_id}
        return short_hash(hash_value({
            "manifest": manifest_body,
            "table": self._table,
            "default": self._default,
        }), 12)

    def reviewer_ref(self) -> dict:
        """The ``reviewer`` block echoed on every verdict — identifies which
        profile judged, for 15's multi-reviewer aggregation."""
        return {"kind": "model_visual", "name": self.manifest_id,
                "profile_digest": self.profile_digest}

    # ---- §6 observation emission (calibration view) -------------------------

    def observe(self, media_sha256: str | None) -> list[dict]:
        """The §6 observation this profile reports for one media, in the
        dimension/observed/severity/allow_unknown shape 15's calibration layer
        scores against a case's ``expected_observations``. Deterministic."""
        s = self.stance_for(media_sha256)
        observed = s["present"]  # the reviewer's call on the media's own dimension
        return [{
            "dimension": s["dimension"],
            "observed": observed,
            "severity": s["severity"],
            "allow_unknown": observed in ("uncertain", "not_evaluated"),
            "message": s["message"],
        }]

    # ---- v2 verdict emission (real intake plumbing) -------------------------

    def build_verdict(self, brief_row: dict) -> dict:
        """Map this reviewer's stance onto ONE brief row's packet expectations →
        a ``manju.qc.verdict/v2`` payload ready for ``record_verdicts``. The
        observed state per expectation is chosen by the expectation's polarity;
        a non-null severity adds one finding. Echoes the packet's binding fields
        exactly (media_sha256 / spec_hash / expectation_digest) so intake binds
        (never re-stamps) — the whole DR02 race contract stays intact."""
        if "packet_id" not in brief_row or "expectations" not in brief_row:
            raise FakeReviewerError(
                "brief row lacks packet fields — call qc_brief on a project whose "
                "shot has a selected take (packet issuance is best-effort)")
        media_sha = (brief_row.get("media") or {}).get("sha256")
        s = self.stance_for(media_sha)
        observations = []
        for e in brief_row["expectations"]:
            observed = s["present"] if e["polarity"] == "present" else s["absent"]
            observations.append({
                "expectation_id": e["id"],
                "observed": observed,
                "evidence_refs": [f"frame:{s['case_id'] or 'default'}"],
            })
        findings = []
        if s["severity"]:
            if s["severity"] not in LEVELS:
                raise FakeReviewerError(f"severity {s['severity']!r} not in {LEVELS}")
            findings.append({"level": s["severity"], "message": s["message"]})
        return {
            "schema": VERDICT_SCHEMA,
            "packet_id": brief_row["packet_id"],
            "subject": {"kind": "shot", "id": brief_row["shot"]},
            "media_sha256": media_sha,
            "spec_hash": brief_row["spec_hash"],
            "expectation_digest": brief_row["expectation_digest"],
            "observations": observations,
            "findings": findings,
            "reviewer": self.reviewer_ref(),
        }

    def verdicts_for_brief(self, brief: dict) -> dict:
        """A whole-brief batch payload (every reviewable shot row) for one
        ``record_verdicts`` call. Rows without packet fields are skipped."""
        verdicts = [self.build_verdict(r) for r in brief.get("shots", [])
                    if "packet_id" in r]
        return {"schema": VERDICT_SCHEMA, "verdicts": verdicts}


# --------------------------------------------------------------- registry wiring


def install_manifests(providers_dir: Path, profiles=PROFILES) -> dict[str, str]:
    """Copy the committed provider manifest(s) into a ``MANJU_PROVIDERS_DIR``
    tree (``<dir>/<id>/provider.yaml``) so ``manifest.load_manifests`` and
    ``qc.content.vision_provider_id`` discover them through the REAL registry.
    Returns {profile: provider_id}. Copying (vs pointing the env at the committed
    tree) keeps tests hermetic and lets a test install only one profile."""
    providers_dir = Path(providers_dir)
    out: dict[str, str] = {}
    for profile in profiles:
        pid = MANIFEST_IDS[profile]
        dest = providers_dir / pid
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_provider_yaml(profile), dest / "provider.yaml")
        out[profile] = pid
    return out

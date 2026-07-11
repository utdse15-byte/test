"""AI_IDE_18 WP1 — voice identity, provenance and the template-export gate.

Voice profile binding lives in the BIBLE source (a character entry is a
free-form dict, so this is purely additive — no ``models.py`` change). A
character's voice profile is the fields that already drive TTS staleness
(``voice / voice_ref / voice_sample / voice_id / tone`` — hashed by
:func:`manju.core.spec.voice_payload`) PLUS the AI_IDE_18 additions:

    voice_provider, voice_model, voice_ref_hash   -- resolved binding identity
    voice_locked: true|false                       -- the explicit LOCK field
    voice_provenance: {source, license_or_consent, note}
                                                   -- where the sample came from
                                                      and the right to use it

Contract §4 / addendum ruling 2 pins, all enforced here:

- Audition is a DISPOSABLE preview, never a take (:data:`AUDITION_IS_PREVIEW` —
  the voice-preview path writes to ``.manju/…`` runtime, not the take store).
- LOCK is an explicit source field; only a locked profile is meant for batch TTS.
- Missing rights info ⇒ the profile still WORKS locally (nothing is deleted or
  blocked at build) but it is FLAGGED and BLOCKS template-pack export
  (:func:`template_export_gate` — the surface AI_IDE_17 consumes).
- A voice-profile change makes downstream dubbing stale — the identity digest
  moves (:func:`profile_digest`), mirroring the ``voice_id``-in-``voice_hash``
  staleness the TTS pipeline already has; history takes are never deleted.
"""

from __future__ import annotations

from typing import Any

from ..core.hashing import hash_value

# Audition never mints a take (contract §4). The voice preview path
# (media/ttspreview.py) renders into disposable runtime; this constant documents
# the invariant a test pins.
AUDITION_IS_PREVIEW = True

# rights fields a shareable template pack requires (addendum ruling 2).
_REQUIRED_RIGHTS = ("source", "license_or_consent")

# identity fields whose change should stale downstream dubbing.
_IDENTITY_FIELDS = ("voice", "voice_ref", "voice_sample", "voice_id", "tone",
                    "voice_provider", "voice_model", "voice_ref_hash")


def _provenance(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalise provenance from ``voice_provenance`` (preferred) or a
    ``voice_sample`` dict carrying its own source/license/hash."""
    prov = entry.get("voice_provenance")
    if not isinstance(prov, dict):
        prov = {}
    sample = entry.get("voice_sample")
    if isinstance(sample, dict):
        # a voice_sample dict may carry the rights inline
        prov = {
            "source": prov.get("source") or sample.get("source"),
            "license_or_consent": (prov.get("license_or_consent")
                                   or sample.get("license") or sample.get("consent")),
            "content_hash": prov.get("content_hash") or sample.get("hash")
            or sample.get("content_hash"),
            "note": prov.get("note") or sample.get("note"),
        }
    return {
        "source": prov.get("source"),
        "license_or_consent": prov.get("license_or_consent"),
        "content_hash": prov.get("content_hash"),
        "note": prov.get("note"),
    }


def voice_profile(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalise a bible character entry into a voice profile, or None when the
    entry declares no voice at all."""
    if not isinstance(entry, dict):
        return None
    voice_keys = ("voice", "voice_ref", "voice_sample", "voice_id", "tone",
                  "voice_provider", "voice_model", "voice_ref_hash",
                  "voice_provenance", "voice_locked")
    if not any(k in entry for k in voice_keys):
        return None
    return {
        "voice": entry.get("voice"),
        "voice_ref": entry.get("voice_ref"),
        "voice_id": entry.get("voice_id"),
        "tone": entry.get("tone"),
        "provider": entry.get("voice_provider"),
        "model": entry.get("voice_model"),
        "ref_hash": entry.get("voice_ref_hash"),
        "locked": bool(entry.get("voice_locked", False)),
        "provenance": _provenance(entry),
    }


def character_profile(project: Any, character_id: str) -> dict[str, Any] | None:
    """The voice profile of a bible character (None if absent/voiceless)."""
    bible = project.load_bible()
    return voice_profile(bible.get(character_id))


def is_locked(profile: dict[str, Any] | None) -> bool:
    return bool(profile and profile.get("locked"))


def provenance_complete(profile: dict[str, Any] | None) -> bool:
    """True when the rights info a shareable template needs is present."""
    if not profile:
        return False
    prov = profile.get("provenance") or {}
    return all(str(prov.get(k) or "").strip() for k in _REQUIRED_RIGHTS)


def template_export_gate(profile: dict[str, Any] | None) -> dict[str, Any]:
    """The gate AI_IDE_17's template-pack export must consult. A voice profile
    with incomplete rights info is NOT shareable — it works locally but must not
    ride into a distributable template pack (contract §4).

    Returns ``{shareable, blocked, reasons[]}``. Being unlocked is a soft
    warning (a locked profile is the intent for sharing), but ONLY missing
    rights is hard-blocking."""
    reasons: list[str] = []
    if profile is None:
        return {"shareable": False, "blocked": True,
                "reasons": ["no voice profile on this character"]}
    prov = profile.get("provenance") or {}
    for field in _REQUIRED_RIGHTS:
        if not str(prov.get(field) or "").strip():
            reasons.append(f"voice_provenance.{field} missing — a shared voice "
                           f"sample must record its {field}")
    blocked = bool(reasons)
    if not is_locked(profile):
        reasons.append("voice profile is not locked (voice_locked: true) — "
                       "audition only; lock before sharing")
    return {"shareable": not blocked, "blocked": blocked, "reasons": reasons}


def profile_digest(profile: dict[str, Any] | None) -> str | None:
    """Identity digest over the staleness-bearing fields. Provenance NOTES do
    not move it (they are rights metadata, not synthesis inputs), but any
    binding-identity change (voice_id / provider / model / ref) does — so
    downstream dubbing goes stale exactly when the VOICE would actually change."""
    if not profile:
        return None
    return hash_value({k: profile.get(_map(k)) for k in _IDENTITY_FIELDS})


def _map(field: str) -> str:
    return {"voice_provider": "provider", "voice_model": "model",
            "voice_ref_hash": "ref_hash"}.get(field, field)


def downstream_stale(old: dict[str, Any] | None, new: dict[str, Any] | None) -> bool:
    """Whether a profile change from ``old`` to ``new`` stales downstream dubbing."""
    return profile_digest(old) != profile_digest(new)


def audit_project(project: Any) -> dict[str, Any]:
    """Whole-bible voice-identity audit: per character, its lock state and
    template-export shareability. The blocked list is what a template export
    would refuse to include."""
    bible = project.load_bible()
    rows: list[dict[str, Any]] = []
    for cid, entry in sorted(bible.items()):
        profile = voice_profile(entry)
        if profile is None:
            continue
        gate = template_export_gate(profile)
        rows.append({
            "character": cid,
            "locked": is_locked(profile),
            "provider": profile.get("provider"),
            "voice_id": profile.get("voice_id"),
            "shareable": gate["shareable"],
            "blocked": gate["blocked"],
            "reasons": gate["reasons"],
        })
    return {
        "characters": rows,
        "blocked_export": [r["character"] for r in rows if r["blocked"]],
    }

"""spec_hash — the staleness anchor (§4.3).

Only fields that affect the rendered picture participate, after canonical
serialization: scene/characters (together with their Bible excerpts), camera,
action, quality, and generation parameters. `status`, `locked`, and notes are
deliberately excluded so that human decisions never make a take look stale.

VERSIONED HASHES (round W, review #37/#16/#60) — §4.3 conservatism extended to
new provider-input fields WITHOUT mass-restaging every existing take:

    SPEC_VERSION  = 5   v4 plus complete list/mapping reference syntax. v4 is
                        retained byte-for-byte for historical take comparison.

    version 2           dialogue {speaker,text} + (when non-empty) keyframes
                        both already flow into the real prompt/provider call
                        (dialogue -> prompt compiler; keyframes -> first/last
                        frame video tasks) but were excluded from v1's payload.
    VOICE_VERSION = 2   the resolved TTS provider id + a manifest fingerprint
                        (endpoint + body template) + language/format — a
                        provider/model/language/format swap must be visible to
                        voice staleness, not just the dialogue line.

``version`` defaults to **1** on every function here — the exact v1 payload,
byte-identical to before this landed (pinned by test_hash_versions.py). Only
callers that opt in (new take generation, staleness comparisons that read a
take's own recorded ``spec_version``/``voice_hash_version``) pass a higher
version. An EXISTING take is judged FOREVER by the version it was generated
under (documented §4.3 conservatism) — bumping SPEC_VERSION/VOICE_VERSION never
mass-invalidates a project; only NEWLY generated takes record the new version
and gain the new sensitivity.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .intent import picture_contract_payload
from .hashing import hash_file, hash_value
from .models import ShotSpec
from .reference_syntax import iter_authored_reference_bindings

SPEC_VERSION = 5
VOICE_VERSION = 2

_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def _bible_excerpt(bible: dict[str, dict[str, Any]], key: str | None) -> Any:
    if key is None:
        return None
    entry = dict(bible.get(key) or {})
    entry.pop("locked", None)  # lock bookkeeping is not part of the picture
    return entry


def _keyframe_image_hash(image: str, project_root: Path | str | None) -> str | None:
    """sha256 of a keyframe's LOCAL file content, when ``image`` resolves to one
    on disk — so editing the referenced image's BYTES (not just its path or
    prompt text) also restages a v2 take. A bible-asset id, an http(s):// URL,
    or a path that does not resolve to an existing file yields ``None`` (the
    `image` string itself still participates in the payload either way)."""
    if not image or _URL_SCHEME_RE.match(image):
        return None
    if project_root is None:
        return None
    p = Path(image)
    if not p.is_absolute():
        p = Path(project_root) / image
    try:
        if p.is_file():
            return hash_file(p)
    except OSError:
        return None
    return None


def _keyframes_payload(shot: ShotSpec, project_root: Path | str | None) -> list[dict[str, Any]]:
    """v2 keyframes payload (review #37/#16): position/at_ms + image + prompt,
    plus ``image_file_hash`` when the image is a resolvable local file. Empty
    when the shot has no keyframes — the caller omits the key entirely in that
    case, keeping a keyframe-less shot's v2 payload shaped exactly like v1's."""
    out: list[dict[str, Any]] = []
    for kf in shot.keyframes:
        entry: dict[str, Any] = {
            "position": kf.position,
            "at_ms": kf.at_ms,
            "image": kf.image,
            "prompt": kf.prompt,
        }
        file_hash = _keyframe_image_hash(kf.image or "", project_root)
        if file_hash is not None:
            entry["image_file_hash"] = file_hash
        out.append(entry)
    return out


def _as_ref_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item not in (None, "")]
    return [value]


def _local_reference_hash(ref: str, project_root: Path | str | None) -> str | None:
    """Hash a project-contained local reference without following it outside."""
    if not ref or _URL_SCHEME_RE.match(ref) or project_root is None:
        return None
    root = Path(project_root).resolve()
    path = Path(ref)
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve()
        resolved.relative_to(root)
        if resolved.is_file():
            return hash_file(resolved)
    except (OSError, ValueError):
        return None
    return None


def _canonical_subject_scope(value: Any, inferred: str | None = None) -> str | None:
    raw = str(value or inferred or "").strip()
    if not raw:
        return None
    for prefix, canonical in (
        ("character:", "character"), ("character/", "character"),
        ("prop:", "prop"), ("prop/", "prop"),
        ("asset:", "asset"), ("asset/", "asset"),
        ("scene:", "scene"), ("scene/", "scene"),
    ):
        if raw.lower().startswith(prefix):
            return f"{canonical}:{raw[len(prefix):].strip()}"
    return raw


def _reference_binding(
    value: Any,
    *,
    kind: str | None,
    tier: str,
    project_root: Path | str | None,
    inferred_scope: str | None = None,
) -> dict[str, Any] | None:
    spec = value if isinstance(value, dict) else {}
    explicit_kind = kind
    if isinstance(value, dict):
        if value.get("video") not in (None, ""):
            ref = value.get("video")
            explicit_kind = "video"
        elif value.get("image") not in (None, ""):
            ref = value.get("image")
            explicit_kind = "image"
        else:
            ref = value.get("ref") or value.get("path")
    else:
        ref = value
    if ref in (None, ""):
        return None
    ref = str(ref)
    if explicit_kind is None:
        explicit_kind = "video" if Path(ref.split("?", 1)[0]).suffix.lower() in {
            ".mp4", ".mov", ".webm", ".m4v", ".mkv", ".gif"
        } else "image"
    return {
        "kind": explicit_kind,
        "ref": ref,
        "tier": tier,
        "controls": [str(item) for item in _as_ref_list(spec.get("controls"))],
        "ignore": [str(item) for item in _as_ref_list(spec.get("ignore"))],
        "subject_scope": _canonical_subject_scope(spec.get("subject_ref"), inferred_scope),
        "local_sha256": _local_reference_hash(ref, project_root),
    }


def _reference_payload_v4(
    shot: ShotSpec,
    bible: dict[str, dict[str, Any]],
    project_root: Path | str | None,
) -> list[dict[str, Any]]:
    """All authored logical reference bindings in stable resolution order."""
    out: list[dict[str, Any]] = []

    def add(values: Any, *, kind: str | None, tier: str,
            inferred_scope: str | None = None) -> None:
        for value in _as_ref_list(values):
            row = _reference_binding(
                value, kind=kind, tier=tier, project_root=project_root,
                inferred_scope=inferred_scope,
            )
            if row is not None:
                out.append(row)

    params = dict(shot.generation.params or {})
    for key in ("image", "images"):
        add(params.get(key), kind="image", tier="params")
    for key in ("video", "videos"):
        add(params.get(key), kind="video", tier="params")
    add(params.get("refs"), kind=None, tier="params")

    add(getattr(shot, "refs", None), kind=None, tier="shot_refs")

    bible_subjects: list[tuple[str, str]] = [
        (str(character_id), f"character:{character_id}")
        for character_id in shot.characters
    ]
    if shot.scene:
        bible_subjects.append((shot.scene, f"scene:{shot.scene}"))
    bible_subjects.extend(
        (str(prop_id), f"prop:{prop_id}") for prop_id in (shot.props or [])
    )
    for bible_id, scope in bible_subjects:
        entry = bible.get(bible_id)
        if not isinstance(entry, dict):
            continue
        for key in ("ref_image", "ref_images"):
            add(entry.get(key), kind="image", tier="bible", inferred_scope=scope)
        for key in ("ref_video", "ref_videos"):
            add(entry.get(key), kind="video", tier="bible", inferred_scope=scope)
    return out


def _reference_payload_v5(
    shot: ShotSpec,
    bible: dict[str, dict[str, Any]],
    project_root: Path | str | None,
) -> list[dict[str, Any]]:
    """Complete authored syntax, shared with provider reference resolution."""
    out: list[dict[str, Any]] = []
    for binding in iter_authored_reference_bindings(shot, bible):
        row = _reference_binding(
            binding.value,
            kind=binding.kind,
            tier=binding.tier,
            project_root=project_root,
            inferred_scope=binding.inferred_scope,
        )
        if row is not None:
            out.append(row)
    return out


def spec_payload(
    shot: ShotSpec,
    bible: dict[str, dict[str, Any]] | None = None,
    *,
    version: int = 1,
    project_root: Path | str | None = None,
) -> dict[str, Any]:
    """The exact dict that gets hashed. Kept as a named function so tests can
    assert what does / does not participate.

    ``version=1`` (default) is byte-identical to the pre-round-W payload.
    ``version=2`` additionally hashes ``dialogue`` and,
    when non-empty, ``keyframes`` — both real provider inputs today. Pass
    ``project_root`` so a keyframe's local image file content can be hashed;
    omitted, the keyframe entry still carries its path/prompt (a rename or
    prompt edit is still caught either way). ``version=3`` adds explicit prop
    dependencies and only the ShotContract fields that affect provider picture
    input. A prompt override suppresses those new default-prompt projections.
    ``version=4`` records the historical flat logical-reference formula.
    ``version=5`` uses the same authored list/mapping expansion as provider
    resolution, including singular/plural image/video keys and mixed ``refs``.
    Provider-specific delivery policy is deliberately absent from both.
    """
    bible = bible or {}
    generation = shot.generation.model_dump(exclude_none=False)
    payload: dict[str, Any] = {
        "scene": shot.scene,
        "scene_bible": _bible_excerpt(bible, shot.scene),
        "characters": shot.characters,
        "character_bible": {c: _bible_excerpt(bible, c) for c in shot.characters},
        "camera": shot.camera.model_dump(),
        "action": shot.action.model_dump(),
        "quality": shot.quality.model_dump(),
        "duration": shot.duration,
        "generation": generation,
    }
    if version >= 2:
        payload["dialogue"] = {"speaker": shot.dialogue.speaker, "text": shot.dialogue.text}
        keyframes = _keyframes_payload(shot, project_root)
        if keyframes:
            payload["keyframes"] = keyframes
    if version >= 3 and not (
        isinstance(shot.generation.prompt_override, str)
        and shot.generation.prompt_override.strip()
    ):
        prop_ids = list(dict.fromkeys(shot.props or []))
        if prop_ids:
            payload["props"] = prop_ids
            payload["prop_bible"] = {
                prop_id: _bible_excerpt(bible, prop_id) for prop_id in prop_ids
            }
        contract_payload = picture_contract_payload(
            shot,
            include_structured_opening=version >= 5,
        )
        if contract_payload:
            payload["contract"] = contract_payload
    if version == 4:
        payload["references"] = _reference_payload_v4(shot, bible, project_root)
    elif version >= 5:
        payload["references"] = _reference_payload_v5(shot, bible, project_root)
    return payload


def compute_spec_hash(
    shot: ShotSpec,
    bible: dict[str, dict[str, Any]] | None = None,
    *,
    version: int = 1,
    project_root: Path | str | None = None,
) -> str:
    return hash_value(spec_payload(shot, bible, version=version, project_root=project_root))


def diff_spec_fields(old: dict[str, Any], new: dict[str, Any], prefix: str = "") -> list[str]:
    """Dotted paths whose values differ between two spec payloads — the
    evidence behind "why is this stale" (a hash can only say THAT the spec
    moved; this says WHERE). Dict-vs-dict recurses; everything else compares
    by equality. Sorted for stable output."""
    fields: list[str] = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        path = f"{prefix}{key}"
        if isinstance(a, dict) and isinstance(b, dict):
            fields.extend(diff_spec_fields(a, b, path + "."))
        else:
            fields.append(path)
    return fields


# --------------------------------------------------------- voice input hash

# FIX-F: dialogue drives the VOICE, not the picture — so it is deliberately
# absent from spec_payload above, and voice takes get their own staleness
# anchor. Only voice-shaping bible fields participate; changing a character's
# look must never invalidate their recorded lines.
VOICE_BIBLE_KEYS = ("voice", "voice_ref", "voice_sample", "voice_id", "tone")


def voice_payload(
    shot: ShotSpec,
    bible: dict[str, dict[str, Any]] | None = None,
    *,
    version: int = 1,
    provider: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What shapes a generated VOICE take (M3 TTS staleness anchor, FIX-F):
    the line itself, who speaks it, and the speaker's voice reference from the
    bible. Camera/action/quality are picture-side and excluded — the exact
    mirror of spec_payload's exclusion of dialogue.

    ``version=2`` (:data:`VOICE_VERSION`, review #60) additionally hashes the
    RESOLVED TTS provider identity — ``provider`` is
    ``{id, fingerprint, language, format}`` (see
    ``providers.tts.voice_provider_descriptor``) — so a provider/model/
    language/output-format swap changes the hash even when the line itself
    did not. ``provider=None`` at v2 still hashes (as all-``None`` fields),
    never crashes: provider resolution is a caller concern, not this
    function's.
    """
    bible = bible or {}
    speaker_entry = bible.get(shot.dialogue.speaker) or {}
    voice_ref = {k: speaker_entry[k] for k in VOICE_BIBLE_KEYS if k in speaker_entry}
    # WP4: locale voices.yaml effective voice_id (via generation.params) must
    # win over bible voice_id for hashing — otherwise English voice overrides
    # never restage locale takes. Absent locale_voice_id → payload shape
    # byte-identical to pre-locale projects.
    params = getattr(getattr(shot, "generation", None), "params", None) or {}
    locale_vid = params.get("locale_voice_id")
    if locale_vid:
        voice_ref = {**voice_ref, "voice_id": str(locale_vid)}
    payload: dict[str, Any] = {
        "text": shot.dialogue.text,
        "speaker": shot.dialogue.speaker,
        "voice_ref": voice_ref,
    }
    if version >= 2:
        provider = provider or {}
        payload["provider"] = provider.get("id")
        payload["manifest_fingerprint"] = provider.get("fingerprint")
        payload["language"] = provider.get("language")
        payload["format"] = provider.get("format")
    return payload


def compute_voice_hash(
    shot: ShotSpec,
    bible: dict[str, dict[str, Any]] | None = None,
    *,
    version: int = 1,
    provider: dict[str, Any] | None = None,
) -> str:
    return hash_value(voice_payload(shot, bible, version=version, provider=provider))

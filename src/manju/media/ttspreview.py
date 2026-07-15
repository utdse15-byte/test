"""TTS 试听 preview — disposable short synthesis, never a take (WP2 / R19).

Synthesizes one dialogue line into ``.manju/webpreview/tts/<key>.mp3`` where
``key = short_hash(cache_key(text, resolved voice descriptor))``. Replay is a
cache hit (zero cost, zero re-synthesis). No ``register_voice_take``, no
sidecar, no event — disposable runtime under ``.manju/`` (§1.9 / §3).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..core.container import Project, ProjectError
from ..core.hashing import cache_key, short_hash
from ..core.models import ShotSpec


class PreviewUnavailable(RuntimeError):
    """TTS cannot produce a preview — surface as 「TTS 不可用」, never a traceback."""


def _preview_dir(project: Project) -> Path:
    d = project.root / ".manju" / "webpreview" / "tts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _voice_descriptor(provider: Any) -> dict[str, Any]:
    from ..providers.tts import voice_provider_descriptor

    return voice_provider_descriptor(provider)


def _resolve_text_and_voice(
    project: Project,
    shot: ShotSpec,
    *,
    text: str | None,
    voice: str | None,
    provider: Any,
) -> tuple[str, str, dict[str, Any]]:
    """Return (text, voice_id, cacheable descriptor)."""
    line = (text if text is not None else shot.dialogue.text) or ""
    line = str(line).strip()
    if not line:
        raise PreviewUnavailable(f"{shot.id} 没有台词,无法试听")
    desc = _voice_descriptor(provider)
    # Prefer explicit voice override, else provider's per-shot resolution
    voice_id = voice
    if not voice_id:
        bible = project.load_bible()
        if hasattr(provider, "_voice_for"):
            try:
                voice_id = str(provider._voice_for(shot, bible))
            except Exception:
                voice_id = None
        if not voice_id:
            tts_cfg = getattr(getattr(provider, "manifest", None), "tts", None)
            voice_id = str(getattr(tts_cfg, "default_voice", None) or "default")
    desc = {**desc, "voice": voice_id, "text": line}
    return line, str(voice_id), desc


def _synthesize_edge(provider: Any, text: str, voice_id: str, dest: Path,
                     *, should_cancel=None) -> None:
    try:
        import edge_tts
    except ImportError as exc:
        raise PreviewUnavailable(
            "TTS 不可用: edge-tts 未安装 — pip install 'manju[edgetts]'"
        ) from exc

    from ..providers.base import ProviderCanceled

    # C31: pre-start cancel before any network stream.
    if should_cancel is not None and should_cancel():
        raise ProviderCanceled(getattr(provider, "id", "edge"), "preview")

    async def _run() -> None:
        communicate = edge_tts.Communicate(
            text, voice_id,
            proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
        )
        with open(dest, "wb") as audio:
            async for chunk in communicate.stream():
                if should_cancel is not None and should_cancel():
                    raise ProviderCanceled(getattr(provider, "id", "edge"), "preview")
                if chunk["type"] == "audio":
                    audio.write(chunk["data"])

    try:
        asyncio.run(_run())
    except ProviderCanceled:
        raise
    except Exception as exc:
        raise PreviewUnavailable(
            f"TTS 不可用: {' '.join(str(exc).split())[:300]}"
        ) from exc
    if not dest.exists() or dest.stat().st_size == 0:
        raise PreviewUnavailable("TTS 不可用: 合成结果为空")


def _synthesize_generic(provider: Any, project: Project, shot: ShotSpec,
                        text: str, dest: Path, *, should_cancel=None) -> None:
    """Reuse GenericTtsProvider's transport by temporarily overriding dialogue
    text on a copy and synthesizing into a temp dir, then copying out —
    WITHOUT calling register_voice_take."""
    import inspect
    from copy import deepcopy

    from ..core.models import ShotSpec as SS
    from ..providers.base import ProviderCanceled

    data = shot.model_dump()
    data["dialogue"] = {**(data.get("dialogue") or {}), "text": text}
    hypo = SS.model_validate(data)
    bible = project.load_bible()

    # Prefer a private synthesize-bytes path when present; else call synthesize
    # into a throwaway project-local temp and strip the registered take after.
    # Generic path: use the same _audio_from machinery via synthesize into a
    # temp Project clone is too heavy — instead monkey the register.
    original_register = project.register_voice_take
    captured: list[Path] = []

    def _capture(shot_id, media_file, sidecar):
        # Copy to dest instead of registering
        import shutil
        shutil.copy2(media_file, dest)
        captured.append(dest)
        return dest  # pretend registration

    try:
        project.register_voice_take = _capture  # type: ignore[method-assign]
        # C31: thread cancel into synthesize when the adapter supports it.
        kwargs = {}
        try:
            if (
                should_cancel is not None
                and "should_cancel" in inspect.signature(provider.synthesize).parameters
            ):
                kwargs["should_cancel"] = should_cancel
        except (TypeError, ValueError):
            pass
        provider.synthesize(project, hypo, bible, **kwargs)
    except ProviderCanceled:
        raise
    except Exception as exc:
        raise PreviewUnavailable(
            f"TTS 不可用: {' '.join(str(exc).split())[:300]}"
        ) from exc
    finally:
        project.register_voice_take = original_register  # type: ignore[method-assign]
    if not captured or not dest.exists() or dest.stat().st_size == 0:
        raise PreviewUnavailable("TTS 不可用: 合成结果为空")


def preview_voice(
    project: Project,
    shot_id: str,
    *,
    text: str | None = None,
    voice: str | None = None,
    provider: str | None = None,
    assume_yes: bool = False,
    should_cancel=None,
) -> dict[str, Any]:
    """Synthesize a disposable preview. Returns
    ``{"preview": relpath, "cached": bool, "path": Path}``.

    Priced TTS goes through spend_gate (per_call). Edge is free.
    ``should_cancel`` (C31): cooperative cancel during synthesis/poll.
    """
    try:
        shot = project.load_shot(shot_id)
    except ProjectError as exc:
        raise PreviewUnavailable(str(exc)) from exc

    from ..media.voicefix import _resolve_tts
    from ..providers.tts import TtsUnavailable

    try:
        provider_id, tts = _resolve_tts(provider)
    except (TtsUnavailable, Exception) as exc:
        raise PreviewUnavailable(
            f"TTS 不可用: {' '.join(str(exc).split())[:300]}"
        ) from exc

    line, voice_id, desc = _resolve_text_and_voice(
        project, shot, text=text, voice=voice, provider=tts
    )
    key = short_hash(cache_key(desc), 16)
    dest = _preview_dir(project) / f"{key}.mp3"
    if dest.exists() and dest.stat().st_size > 0:
        return {
            "preview": project.relpath(dest) if hasattr(project, "relpath") else str(dest),
            "cached": True,
            "path": dest,
            "provider": provider_id,
        }

    # Spend gate for priced TTS (Edge per_call=0 skips meaningfully)
    manifest = getattr(tts, "manifest", None)
    cost = getattr(manifest, "cost", None) if manifest is not None else None
    if cost is not None and float(getattr(cost, "per_call", 0) or 0) > 0:
        from ..build.graph import WaitingUser, spend_gate

        try:
            spend_gate(
                project, float(cost.per_call), cost.currency,
                assume_yes=assume_yes,
                hint=f"确认后重试: manju voice {shot_id} --preview --yes",
            )
        except WaitingUser:
            # C72: re-raise for GUI voice_preview job to map to waiting_user.
            raise

    with tempfile.TemporaryDirectory(prefix=f"ttsprev_{shot_id}_") as tmp:
        tmp_dest = Path(tmp) / "preview.mp3"
        # Edge path vs generic
        adapter = getattr(manifest, "adapter", "") if manifest else ""
        if "edge_tts" in str(type(tts).__module__) or "edge" in str(adapter).lower():
            _synthesize_edge(tts, line, voice_id, tmp_dest, should_cancel=should_cancel)
        elif hasattr(tts, "synthesize"):
            # write as mp3/wav then copy; generic may use other formats
            fmt = (getattr(getattr(manifest, "tts", None), "audio_format", None) or "mp3")
            tmp_dest = Path(tmp) / f"preview.{str(fmt).lstrip('.')}"
            _synthesize_generic(
                tts, project, shot, line, tmp_dest, should_cancel=should_cancel)
        else:
            raise PreviewUnavailable("TTS 不可用: 供应商没有 synthesize 接口")

        # Atomic place into cache
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_dest, dest) if tmp_dest.suffix == dest.suffix else (
            dest.write_bytes(tmp_dest.read_bytes())
        )

    return {
        "preview": project.relpath(dest),
        "cached": False,
        "path": dest,
        "provider": provider_id,
    }

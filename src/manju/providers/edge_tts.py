"""Edge TTS adapter — a REAL, keyless cloud TTS (competitive-study round J).

Every mature auto-video pipeline studied (MoneyPrinterTurbo, ShortGPT) ships
Microsoft Edge's free neural TTS as the default voice, via the ``edge-tts``
package: no account, no API key, dozens of zh-CN neural voices. This adapter
brings the same capability to Manju as a DEDICATED adapter class — the §8.6
``module:Class`` escape hatch proven with a real provider (Edge TTS speaks
WebSocket, which the generic REST adapter deliberately does not).

Manifest::

    # ~/.manju/providers/edge/provider.yaml
    id: edge
    type: tts
    adapter: manju.providers.edge_tts:EdgeTtsProvider
    tts: {default_voice: zh-CN-YunxiNeural, audio_format: mp3}
    cost: {per_call: 0.0, currency: CNY}          # it is free

Voice resolution: the speaker's bible ``voice_id`` (e.g. an Edge ShortName
like ``zh-CN-XiaoxiaoNeural``) wins; else ``tts.default_voice``. Corporate/
egress TLS interception is honored the standard way — the proxy CA must be in
the trust store (``SSL_CERT_FILE``); verification is never disabled.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from ..core.container import Project
from ..core.models import RemoteJobInfo, ShotSpec, VoiceTakeSidecar
from ..core.spec import compute_voice_hash, voice_payload
from .base import FailureKind, ProviderFailure
from .manifest import ProviderManifest


class EdgeTtsProvider:
    """Same surface as GenericTtsProvider: synthesize() registers a voice take."""

    def __init__(self, manifest: ProviderManifest, **_ignored):
        self.manifest = manifest
        self.id = manifest.id

    def _voice_for(self, shot: ShotSpec, bible: dict) -> str:
        payload = voice_payload(shot, bible)
        explicit = payload["voice_ref"].get("voice_id")
        if explicit:
            return str(explicit)
        default = getattr(self.manifest.tts, "default_voice", None)
        return str(default) if default else "zh-CN-XiaoxiaoNeural"

    def synthesize(self, project: Project, shot: ShotSpec, bible: dict) -> Path:
        if not shot.dialogue.text:
            raise ProviderFailure(
                FailureKind.invalid, f"{self.id}: shot {shot.id} has no dialogue to voice"
            )
        try:
            import edge_tts
        except ImportError as exc:
            raise ProviderFailure(
                FailureKind.invalid,
                f"{self.id}: edge-tts is not installed — pip install 'manju[edgetts]'",
            ) from exc

        voice = self._voice_for(shot, bible)
        fmt = (self.manifest.tts.audio_format or "mp3").lstrip(".")

        async def _run(dest: Path) -> None:
            communicate = edge_tts.Communicate(
                shot.dialogue.text, voice,
                proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
            )
            await communicate.save(str(dest))

        with tempfile.TemporaryDirectory(prefix=f"edge_{shot.id}_") as tmp:
            dest = Path(tmp) / f"voice.{fmt}"
            try:
                asyncio.run(_run(dest))
            except Exception as exc:  # network/TLS/service — classify, keep reason
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: Edge TTS synthesis failed — {' '.join(str(exc).split())[:300]}",
                ) from exc
            if not dest.exists() or dest.stat().st_size == 0:
                raise ProviderFailure(
                    FailureKind.provider_error, f"{self.id}: Edge TTS produced no audio"
                )
            sidecar = VoiceTakeSidecar(
                provider=self.id,
                voice_hash=compute_voice_hash(shot, bible),
                params={"text": shot.dialogue.text, "speaker": shot.dialogue.speaker,
                        "voice": voice},
                remote=RemoteJobInfo(job_id=None, cost=None,
                                     currency=self.manifest.cost.currency),
            )
            return project.register_voice_take(shot.id, dest, sidecar)

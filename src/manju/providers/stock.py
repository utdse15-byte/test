"""Stock-footage provider (competitive-study adoption #2).

MoneyPrinterTurbo and ShortGPT source their visuals from royalty-free stock
APIs (Pexels/Pixabay) matched to the script — the single biggest visual
upgrade over cards for talk-driven shorts. This adapter brings the same
capability as a Manju provider: a manifest with capability ``stock_footage``
slots straight into shots' fallback chains (capability routing already
resolves unknown steps to manifest providers).

Search shape (GET + header key) is not the generic submit/poll REST shape,
so this is a dedicated ``module:Class`` adapter::

    # ~/.manju/providers/pexels/provider.yaml
    id: pexels
    type: video
    adapter: manju.providers.stock:PexelsStockProvider
    capabilities: [stock_footage]
    auth: {key_env: PEXELS_API_KEY}       # free tier: pexels.com/api
    cost: {per_call: 0.0, currency: CNY}  # royalty-free

Query resolution per shot: ``generation.params.query`` wins, else the scene's
bible ``stock_query``/``name``, else the shot's action line. Every take's
lineage records the query and the source URL (§4.2).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from ..core.container import TakeInfo
from .base import (
    FailureKind,
    GenerationRequest,
    Provider,
    ProviderFailure,
    record_provider_failure,
)
from .generic_cloud import Transport, default_transport, reject_html_error_page
from .manifest import ProviderManifest

SEARCH_URL = "https://api.pexels.com/videos/search"


class PexelsStockProvider(Provider):
    kind = "cloud"

    def __init__(self, manifest: ProviderManifest, *, transport: Transport | None = None):
        self.manifest = manifest
        self.id = manifest.id
        self._transport = transport or default_transport

    # ------------------------------------------------------------- helpers

    def _api_key(self) -> str:
        env = self.manifest.auth.key_env or "PEXELS_API_KEY"
        key = os.environ.get(env)
        if not key:
            raise ProviderFailure(
                FailureKind.invalid,
                f"{self.id}: API key env var {env} is not set "
                "(free key at pexels.com/api; keys never live in the project, §8.2)",
            )
        return key

    def _query_for(self, req: GenerationRequest) -> str:
        """Most-specific wins: explicit param > the scene's declared stock
        intent > the shot's action line (describes what is ON SCREEN) > the
        generic scene name."""
        explicit = req.params.get("query")
        if explicit:
            return str(explicit)
        scene = (req.bible or {}).get(req.shot.scene or "") or {}
        if scene.get("stock_query"):
            return str(scene["stock_query"])
        if req.shot.action.main:
            return req.shot.action.main
        if scene.get("name"):
            return str(scene["name"])
        return req.shot.id

    def _pick_file(self, video: dict, *, width: int, height: int) -> dict | None:
        """Choose the rendition closest to the project resolution with the
        right orientation."""
        want_portrait = height >= width
        candidates = []
        for f in video.get("video_files", []):
            fw, fh = f.get("width") or 0, f.get("height") or 0
            if fw <= 0 or fh <= 0:
                continue
            if (fh >= fw) != want_portrait:
                continue
            candidates.append((abs(fw * fh - width * height), f))
        if not candidates:
            return None
        return min(candidates, key=lambda pair: pair[0])[1]

    # ------------------------------------------------------------ Provider

    def generate(self, req: GenerationRequest) -> list[TakeInfo]:
        # F2: a plain Provider on the shot fallback chain records its OWN
        # terminal failures (goal 10) — the registry's fallback walker does not.
        # So a stock search/download failure lands in reports/failures.jsonl in
        # the SAME shape comfyui/local_cmd use, keyed to the shot; the exception
        # type and message are untouched (only the structured record is added).
        try:
            return self._generate(req)
        except ProviderFailure as exc:
            record_provider_failure(req.project, req.shot.id, self.id, exc)
            raise

    def _generate(self, req: GenerationRequest) -> list[TakeInfo]:
        key = self._api_key()
        config = req.project.load_config()
        query = self._query_for(req)
        orientation = "portrait" if config.height >= config.width else "landscape"
        url = SEARCH_URL + "?" + urlencode({
            "query": query,
            "orientation": orientation,
            "per_page": max(1, req.candidates),
        })
        resp = self._transport("GET", url, {"Authorization": key}, None)
        if resp.status == 429:
            raise ProviderFailure(FailureKind.rate_limited,
                                  f"{self.id}: rate limited by the stock API")
        if resp.status >= 400:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: search failed with HTTP {resp.status}",
                detail={"query": query, "body": resp.text()[:500]},
            )
        # A 2xx whose body is not readable JSON (UnicodeDecodeError /
        # JSONDecodeError — both ValueError) or is valid JSON but not an OBJECT
        # (a bare array/string -> no ``.get``) must become a RECORDED
        # ProviderFailure, not an uncaught crash that bypasses the failures.jsonl
        # contract (F2) and aborts the build.
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: search response was not readable JSON",
                detail={"query": query, "body": resp.text()[:500]},
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: search response was not a JSON object",
                detail={"query": query, "body": resp.text()[:500]},
            )
        videos = payload.get("videos", [])
        if not videos:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: no stock footage found for query {query!r} — "
                "set generation.params.query or the scene's stock_query",
            )

        takes: list[TakeInfo] = []
        with tempfile.TemporaryDirectory(prefix=f"stock_{req.shot.id}_") as tmp:
            for i, video in enumerate(videos[: max(1, req.candidates)]):
                chosen = self._pick_file(video, width=config.width, height=config.height)
                if chosen is None:
                    continue
                # A rendition entry may omit (or null) "link"; read it defensively
                # and skip this candidate rather than KeyError-crashing the whole
                # generate() (and bypassing failure recording).
                link = chosen.get("link")
                if not link:
                    continue
                dl = self._transport("GET", link, {}, None)
                if dl.status >= 400 or not dl.body:
                    continue
                # F11: a 200 that is actually an HTML/error page (an expired CDN
                # link, a rate-limit interstitial, a login wall) must never be
                # registered as a poisoned .mp4 take — the SAME guard
                # comfyui/generic_cloud/tts already apply. Raises
                # ProviderFailure(provider_error) with a verbatim snippet, so the
                # failure is diagnosable from evidence instead of silently
                # poisoning the shot with an HTML "video".
                reject_html_error_page(dl, link, self.id)
                dest = Path(tmp) / f"stock_{i:02d}.mp4"
                dest.write_bytes(dl.body)
                takes.append(self._register(
                    req, dest,
                    params={
                        "query": query,
                        "source_url": video.get("url", ""),
                        "video_id": video.get("id"),
                        "rendition": f"{chosen.get('width')}x{chosen.get('height')}",
                    },
                ))
        if not takes:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: found {len(videos)} result(s) for {query!r} but none "
                "matched the project orientation/resolution or downloaded cleanly",
            )
        return takes

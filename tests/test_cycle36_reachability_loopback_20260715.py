"""Cycle-36: reachability_probe loopback bypasses HTTP_PROXY."""

from __future__ import annotations

from pathlib import Path

from manju.providers.manifest import COMFYUI_ADAPTER, ProviderManifest, reachability_probe


def test_reachability_source_uses_loopback_no_proxy() -> None:
    import manju.providers.manifest as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "_is_loopback_host" in src
    assert "ProxyHandler({})" in src


def test_reachability_comfyui_loopback_uses_empty_proxy(monkeypatch) -> None:
    import urllib.request
    import manju.providers.manifest as mod

    seen = {"proxy_empty": False}

    def fake_build_opener(*handlers):
        for h in handlers:
            if isinstance(h, urllib.request.ProxyHandler) and not getattr(h, "proxies", None):
                seen["proxy_empty"] = True

        class Opener:
            def open(self, url, timeout=None):
                class R:
                    status = 200

                    def close(self):
                        pass

                return R()

        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)

    m = ProviderManifest.model_validate({
        "id": "comfyui",
        "type": "video",
        "adapter": COMFYUI_ADAPTER,
        "capabilities": ["image_to_video"],
        "cost": {"per_call": 0.0, "currency": "CNY"},
        "comfyui": {
            "base_url": "http://127.0.0.1:8188",
            "workflow_file": "comfyui/wf.json",
            "input_map": {},
        },
    })
    ok, detail = reachability_probe(m)
    assert ok is True
    assert seen["proxy_empty"] is True
    assert "127.0.0.1" in detail

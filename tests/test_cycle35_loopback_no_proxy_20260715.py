"""Cycle-35: loopback URLs bypass system HTTP_PROXY in default_transport."""

from __future__ import annotations

import manju.providers.generic_cloud as gc
from manju.providers.generic_cloud import _is_loopback_host, default_transport


def test_is_loopback_host() -> None:
    assert _is_loopback_host("localhost")
    assert _is_loopback_host("127.0.0.1")
    assert _is_loopback_host("127.0.0.9")
    assert _is_loopback_host("::1")
    assert _is_loopback_host("[::1]")
    assert not _is_loopback_host("example.com")
    assert not _is_loopback_host("10.0.0.1")
    assert not _is_loopback_host(None)


class _FakeResp:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, body: bytes = b'{"ok":true}'):
        self._body = body
        self._done = False

    def read(self, n=-1):
        if self._done:
            return b""
        self._done = True
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_default_transport_loopback_uses_empty_proxy(monkeypatch) -> None:
    """When targeting 127.0.0.1, open via ProxyHandler({}) not env proxy."""
    seen = {"proxy_empty": False, "urlopen": False}

    def fake_build_opener(*handlers):
        for h in handlers:
            if isinstance(h, gc.urllib.request.ProxyHandler):
                # Empty dict → no proxies for any scheme
                if not getattr(h, "proxies", None):
                    seen["proxy_empty"] = True

        class Opener:
            def open(self, req, timeout=None):
                return _FakeResp()

        return Opener()

    def fake_urlopen(*a, **k):
        seen["urlopen"] = True
        return _FakeResp()

    monkeypatch.setattr(gc.urllib.request, "build_opener", fake_build_opener)
    monkeypatch.setattr(gc.urllib.request, "urlopen", fake_urlopen)

    resp = default_transport("GET", "http://127.0.0.1:8188/history/x", {}, None)
    assert resp.status == 200
    assert seen["proxy_empty"] is True
    assert seen["urlopen"] is False


def test_default_transport_remote_uses_urlopen(monkeypatch) -> None:
    seen = {"urlopen": False}

    def fake_urlopen(req, timeout=None):
        seen["urlopen"] = True
        return _FakeResp(b"{}")

    monkeypatch.setattr(gc.urllib.request, "urlopen", fake_urlopen)
    resp = default_transport("GET", "https://api.example.com/v1/x", {}, None)
    assert resp.status == 200
    assert seen["urlopen"] is True


def test_source_documents_c35() -> None:
    from pathlib import Path
    src = Path(gc.__file__).read_text(encoding="utf-8")
    assert "_is_loopback_host" in src
    assert "ProxyHandler({})" in src

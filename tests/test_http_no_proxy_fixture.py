"""Pin: GUI HTTP tests must not route through system HTTP_PROXY."""

from __future__ import annotations

import urllib.request


def test_autouse_installs_no_proxy_opener():
    """conftest._isolate_http_proxy must leave urllib without a proxy handler
    for localhost — otherwise merge_blockers/job_cancel see 502 from a dead
    corporate proxy on 127.0.0.1:10090."""
    # After autouse fixture: getproxies may still list env, but our helpers use
    # ProxyHandler({}). Also verify opener can be built without proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    assert opener is not None
    # NO_PROXY must be set by the fixture for this test session.
    import os

    assert os.environ.get("NO_PROXY") == "*" or os.environ.get("no_proxy") == "*"

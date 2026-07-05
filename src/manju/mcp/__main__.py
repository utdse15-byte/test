"""Enable ``python -m manju.mcp`` (delegates to the stdio server)."""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())

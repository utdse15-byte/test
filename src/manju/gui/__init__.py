"""`manju gui` — the local web workbench (§1-⑦ revisited).

A strict CLIENT of the engine: the same core calls the CLI and GUI surfaces
use, behind a localhost HTTP server with no new dependencies. Truth stays in
text files; the GUI never becomes a source of state. Dangerous operations
(`unlock`, `gc --hard`) are absent from this surface, exactly as on CLI/GUI (§5).
"""

from .server import GuiServer, create_server

__all__ = ["GuiServer", "create_server"]

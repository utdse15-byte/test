"""MCP server for Manju One (§5, §10, §11).

A THIN wrapper over the exact same core the CLI drives — Claude Code can go
through MCP or "read files + run CLI"; the two paths are equivalent (§11).

Hard design rule: dangerous commands (``unlock``, ``gc``, ``pack``, ``import``)
are NOT on the MCP surface. Changing a lock is human CLI work (§5); the agent's
legitimate channel for a locked-content change is a proposal (the ``propose``
tool).
"""

from __future__ import annotations

__all__ = ["server", "tools"]

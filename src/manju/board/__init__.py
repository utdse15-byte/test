"""Static Review Board (§1-⑦, §11): a self-contained ``board.html`` that
replaces the director's workbench GUI at a fraction of the cost."""

from __future__ import annotations

from .board import generate_board

__all__ = ["generate_board"]

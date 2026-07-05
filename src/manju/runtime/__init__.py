"""Runtime state package (§1-⑥, §3).

SQLite that holds ONLY rebuildable state — task/run bookkeeping and the cost
ledger. Delete the whole ``.manju/`` runtime directory and everything here can
be reconstructed from the text truth + media on disk (§3).
"""

from __future__ import annotations

from .state import RuntimeState

__all__ = ["RuntimeState"]

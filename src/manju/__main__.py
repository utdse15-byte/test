"""``python -m manju`` — the PATH-independent invocation.

Round-2 UX journey finding: on Windows the console-script shim is exactly the
fragile piece (unactivated venv, py.exe launcher, a stale PATH after
update-manju.ps1 -Rollback), and the standard fallback every Python user
reaches for — ``python -m manju`` — answered "No module named manju.__main__".
Two lines close it; ``prog_name`` keeps --help reading ``manju``, not
``__main__.py``.
"""

from .cli import app

app(prog_name="manju")

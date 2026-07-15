import re
from pathlib import Path

def test_no_shell_true_in_src():
    bad = []
    for p in Path("src/manju").rglob("*.py"):
        t = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(t.splitlines(), 1):
            if "shell=True" not in line:
                continue
            s = line.strip()
            if s.startswith("#") or s.startswith('"""') or "never" in s or "``shell=True``" in s:
                continue
            if re.search(r"shell\s*=\s*True", s):
                bad.append(f"{p}:{i}:{s}")
    assert not bad, bad

"""Read-only delivery-file verification using only Python's standard library.

Check integrity, not runtime quality or a publisher's digital signature.
No uploads, installation, automatic extraction or file changes.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import stat

def pairs(items):
    obj = {}
    for k, v in items:
        if k in obj:
            raise ValueError("Duplicate manifest key")
        obj[k] = v
    return obj

def verify(root: Path) -> dict:
    root = root.resolve()
    mf = root / "DELIVERY_MANIFEST.json"
    if mf.is_symlink() or not mf.is_file() or mf.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Missing or invalid DELIVERY_MANIFEST.json")
    data = json.loads(mf.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    if data.get("schema") != "manju.download-delivery/v1" or not isinstance(data.get("files"), dict) or not data["files"]:
        raise ValueError("Unsupported or empty delivery manifest")
    errors = []
    checked = 0
    for name, expected in data["files"].items():
        p = PurePosixPath(name)
        if not name or p.is_absolute() or p.as_posix() != name or ".." in p.parts or "\\" in name or ":" in name:
            raise ValueError("Unsafe declared path")
        target = root
        for part in p.parts:
            target = target / part
            if not target.exists():
                errors.append("Missing: " + name)
                break
            info = target.lstat()
            if stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400):
                raise ValueError("Refuse symlink/reparse point: " + name)
        else:
            if not target.is_file():
                errors.append("Not a file: " + name)
                continue
            if not isinstance(expected, dict) or not isinstance(expected.get("sha256"), str) or len(expected["sha256"]) != 64:
                raise ValueError("Invalid file hash entry")
            digest = hashlib.sha256()
            with target.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if target.stat().st_size != expected.get("bytes") or digest.hexdigest() != expected["sha256"]:
                errors.append("Changed: " + name)
            checked += 1
    return {"ok": not errors, "stage": data.get("runtime_stage"), "files_checked": checked,
            "errors": errors, "scope": "Declared original files only. Extra files are not certified. No runtime or Windows certification."}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        result = verify(args.root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    except Exception as exc:
        print("DELIVERY CHECK FAILED: " + str(exc), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())

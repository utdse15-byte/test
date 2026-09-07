"""Verify an extracted Manju package using only Python's standard library.

Hash inventory is integrity evidence, not a publisher signature. Use a separately
saved archive SHA-256 receipt to identify the download. Mutable installation and
workspace directories are explicitly outside this immutable package inventory.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import unicodedata
import zipfile

LOCAL_DIRS = {'.venv', 'workspace', 'local-logs', '__pycache__'}
HASH = re.compile(r'^[a-f0-9]{64}$')
COMMIT = re.compile(r'^[a-f0-9]{40}$')
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def read_json(path: Path) -> dict:
    if path.stat().st_size > 16*1024*1024:
        raise ValueError('Package metadata exceeds 16 MiB')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key in package metadata')
            result[key] = value
        return result
    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))
    if not isinstance(value, dict):
        raise ValueError('Package metadata must be an object')
    return value


def safe_name(name: str) -> None:
    if not isinstance(name, str):
        raise ValueError('A package path must be a string')
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or path.as_posix() != name or '\\' in name
        or any(ord(c) < 32 for c in name)
        or any(part in {'', '.', '..'} or part.endswith((' ', '.'))
               or any(c in part for c in ':*?"<>|') or part.split('.')[0].upper() in RESERVED
               for part in path.parts)):
        raise ValueError(f'Unsafe portable path: {name!r}')


def is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def regular_member(root: Path, name: str) -> Path:
    safe_name(name)
    current = root
    for part in PurePosixPath(name).parts:
        current = current / part
        if is_link(current):
            raise ValueError(f'Link or reparse point: {name}')
    if not current.is_file():
        raise ValueError(f'Not a regular package member: {name}')
    return current


def inventory(root: Path) -> set[str]:
    files, folded = set(), set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in list(dirs) + names:
            path = base / name
            rel = path.relative_to(root).as_posix()
            safe_name(rel)
            if is_link(path):
                raise ValueError(f'Link or reparse point: {rel}')
            key = unicodedata.normalize('NFC', rel).casefold()
            if key in folded:
                raise ValueError('Case/Unicode collision in package')
            folded.add(key)
            if base == root and name in LOCAL_DIRS:
                if name not in dirs:
                    raise ValueError(f'Local workspace name is not a directory: {name}')
                dirs.remove(name)
                continue
            if name in names:
                if not path.is_file():
                    raise ValueError(f'Not a regular package file: {rel}')
                files.add(rel)
    return files


def validate_wheel(path: Path, expected: dict[str, str]) -> int:
    runtime, folded = {}, set()
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename.rstrip('/') if info.is_dir() else info.filename
            safe_name(name)
            key = unicodedata.normalize('NFC', name).casefold()
            if key in folded:
                raise ValueError('Duplicate or colliding wheel member')
            folded.add(key)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Wheel contains a symbolic link')
            if not info.is_dir() and info.filename.startswith('manju/'):
                if info.file_size > 64*1024*1024:
                    raise ValueError('A runtime wheel member exceeds the local verification limit')
                runtime[info.filename] = hashlib.sha256(archive.read(info)).hexdigest()
        if archive.testzip() is not None:
            raise ValueError('Wheel CRC failed')
    if not expected or runtime != expected:
        raise ValueError('Runtime wheel differs from source')
    return len(runtime)


def verify(root: Path, *, git: bool = False) -> dict:
    root = root.resolve()
    sums = read_json(regular_member(root, 'SHA256SUMS.json'))
    if not sums or 'SHA256SUMS.json' in sums:
        raise ValueError('Invalid self-referential or empty hash manifest')
    names, folded = set(), set()
    for name, expected in sums.items():
        safe_name(name)
        if PurePosixPath(name).parts[0] in LOCAL_DIRS:
            raise ValueError('Mutable local directories cannot be declared as release payload')
        key = unicodedata.normalize('NFC', name).casefold()
        if key in folded:
            raise ValueError('Case/Unicode collision in declared paths')
        folded.add(key)
        names.add(name)
        if not isinstance(expected, str) or not HASH.fullmatch(expected):
            raise ValueError('Invalid SHA-256 in package manifest')
        if digest(regular_member(root, name)) != expected:
            raise ValueError(f'Hash mismatch: {name}')
    actual = inventory(root)
    if actual != names | {'SHA256SUMS.json'}:
        raise ValueError('Missing or unexpected package files: ' + str(sorted(actual ^ (names | {'SHA256SUMS.json'}))[:8]))
    required = {'PACKAGE.json', 'VERIFY_PACKAGE.py', 'repository.bundle', 'CHANGES_FROM_R2.patch', 'START_HERE.md', 'source/DELIVERY_VERSION.json'}
    if not required.issubset(names):
        raise ValueError('Missing recovery or identity payload')
    meta = read_json(root / 'PACKAGE.json')
    version = read_json(root / 'source/DELIVERY_VERSION.json')
    for key in ('stage', 'version', 'baseline', 'inherits', 'features', 'original_r3_r4_bytes_recovered', 'formal_windows_release'):
        if meta.get(key) != version.get(key):
            raise ValueError('Package and source version identity differ: ' + key)
    if meta.get('stage') not in {'R3','R4','R5','R6','R7'}:
        raise ValueError('Unknown delivery stage')
    for key in ('git_head','git_tree','baseline'):
        if not isinstance(meta.get(key), str) or not COMMIT.fullmatch(meta[key]):
            raise ValueError('Invalid Git object identity')
    expected_inherits = [f'R{i}' for i in range(2,int(meta['stage'][1:]))]
    if meta.get('inherits') != expected_inherits:
        raise ValueError('Cumulative stage history is not explicit or contiguous')
    safe_name(meta['wheel'])
    if meta['wheel'] not in names or not meta['wheel'].startswith('wheels/') or not meta['wheel'].endswith('.whl'):
        raise ValueError('Wheel is not a declared installation payload')
    expected_runtime = {p[len('source/src/'):]: sums[p] for p in names if p.startswith('source/src/manju/')}
    count = validate_wheel(root / meta['wheel'], expected_runtime)
    result = {'ok': True, 'stage': meta['stage'], 'git_head': meta['git_head'], 'checked_files': len(sums),
              'wheel_runtime_files': count, 'git_restore_checked': False, 'patch_replay_checked': False,
              'stage_ancestry_checked': False,
              'note': 'Integrity, not a publisher signature, Windows certification or client download confirmation.'}
    if git:
        def run(*args, cwd=None):
            return subprocess.check_output(['git','-c','core.fsmonitor=false',*args], cwd=cwd,
                                           stderr=subprocess.STDOUT, timeout=120)
        with tempfile.TemporaryDirectory(prefix='manju-verify-') as temp:
            clone = Path(temp) / 'restore'
            run('clone','--quiet',str(root/'repository.bundle'),str(clone))
            if run('rev-parse','HEAD',cwd=clone).decode().strip() != meta['git_head']:
                raise ValueError('Git HEAD mismatch')
            run('fsck','--full',cwd=clone)
            tracked = {name for name in run('ls-files','-z',cwd=clone).decode().split('\0') if name}
            exported = {name[len('source/'):] for name in names if name.startswith('source/')}
            if tracked != exported:
                raise ValueError('Git/source file inventory mismatch')
            for name in tracked:
                if digest(regular_member(clone,name)) != sums['source/'+name]:
                    raise ValueError('Git/source byte mismatch: ' + name)
            tree = run('rev-parse','HEAD^{tree}',cwd=clone).decode().strip()
            if tree != meta['git_tree']:
                raise ValueError('Git tree identity mismatch')
            run('merge-base','--is-ancestor',meta['baseline'],meta['git_head'],cwd=clone)
            for stage in expected_inherits[1:]:
                tag = 'delivery/' + stage.lower() + ('-rebuilt' if stage in {'R3','R4'} else '')
                run('merge-base','--is-ancestor',tag,meta['git_head'],cwd=clone)
            run('checkout','--quiet','--detach',meta['baseline'],cwd=clone)
            run('apply','--index',str(root/'CHANGES_FROM_R2.patch'),cwd=clone)
            if run('write-tree',cwd=clone).decode().strip() != tree:
                raise ValueError('Baseline patch replay differs from final tree')
            result.update(git_restore_checked=True, patch_replay_checked=True, stage_ancestry_checked=True,
                          history_commits=int(run('rev-list','--count','--all',cwd=clone)))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--git',action='store_true',help='Restore history and replay patch; requires Git.')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.root,git=args.git),ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print('PACKAGE VERIFICATION FAILED: ' + str(exc),file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

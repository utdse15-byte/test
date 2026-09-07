"""Check a downloaded ZIP, optionally extracting into a NEW directory. No code execution."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import unicodedata
import zipfile

MAX_TOTAL = 1024*1024*1024
MAX_FILE = 512*1024*1024
MAX_FILES = 20000
RESERVED = {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}


def checked_name(name: str) -> str:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or path.as_posix()!=name or '\\' in name
        or any(ord(c)<32 for c in name)
        or any(part in {'','.','..'} or part.endswith((' ','.'))
               or part.split('.')[0].upper() in RESERVED or any(c in part for c in ':*?"<>|')
               for part in path.parts)):
        raise ValueError('Unsafe ZIP member: ' + repr(name))
    return name


def inspect_archive(archive: Path, *, expected_sha256: str | None = None, extract_to: Path | None = None) -> dict:
    if expected_sha256 is not None and not re.fullmatch('[a-fA-F0-9]{64}', expected_sha256):
        raise ValueError('Expected SHA-256 must be exactly 64 hexadecimal characters')
    value = hashlib.sha256()
    with archive.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):
            value.update(block)
        actual_hash = value.hexdigest()
        if expected_sha256 is not None and actual_hash != expected_sha256.lower():
            raise ValueError('Downloaded ZIP SHA-256 does not match the saved receipt')
        source.seek(0)
        with zipfile.ZipFile(source) as z:
            infos = z.infolist()
            if len(infos)>MAX_FILES or sum(i.file_size for i in infos)>MAX_TOTAL:
                raise ValueError('ZIP exceeds the 20,000 members / 1 GiB local extraction limit')
            names, files, spelling = {}, set(), {}
            for info in infos:
                name = checked_name(info.filename[:-1] if info.is_dir() else info.filename)
                parts = name.split('/')
                for depth in range(1,len(parts)+1):
                    prefix = '/'.join(parts[:depth])
                    folded = unicodedata.normalize('NFC',prefix).casefold()
                    if folded in spelling and spelling[folded] != prefix:
                        raise ValueError('Inconsistent case/Unicode spelling in ZIP path components')
                    spelling[folded] = prefix
                key = unicodedata.normalize('NFC',name).casefold()
                if key in names:
                    raise ValueError('Duplicate or case/Unicode-colliding ZIP path')
                names[key] = name
                if info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError('Encrypted or symbolic-link ZIP members are not accepted')
                if info.file_size>MAX_FILE:
                    raise ValueError('ZIP member exceeds the 512 MiB local limit')
                if not info.is_dir():
                    files.add(key)
            for key in names:
                parts = key.split('/')
                if any('/'.join(parts[:i]) in files for i in range(1,len(parts))):
                    raise ValueError('A ZIP file is also used as a parent directory')
            if z.testzip() is not None:
                raise ValueError('ZIP CRC failed')
            if extract_to is not None:
                extract_to = extract_to.absolute()
                extract_to.mkdir(parents=False,exist_ok=False)
                try:
                    for info in infos:
                        target = extract_to / checked_name(info.filename[:-1] if info.is_dir() else info.filename)
                        if info.is_dir():
                            target.mkdir(parents=True,exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True,exist_ok=True)
                            with z.open(info) as reader, target.open('xb') as writer:
                                shutil.copyfileobj(reader,writer,1024*1024)
                except BaseException:
                    shutil.rmtree(extract_to,ignore_errors=True)
                    raise
    return {'ok':True,'archive_sha256':actual_hash,'saved_receipt_matched':expected_sha256 is not None,
            'members':len(infos),'uncompressed_bytes':sum(i.file_size for i in infos),
            'zip_crc_passed':True,'safe_portable_paths_checked':True,'extracted_to_new_directory':extract_to is not None,
            'code_executed':False,'client_download_confirmed':False,
            'note':'Use a separately saved hash to identify a download. CRC alone does not authenticate its publisher.'}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--extract-to',type=Path)
    args=parser.parse_args(argv)
    try:
        print(json.dumps(inspect_archive(args.archive,expected_sha256=args.sha256,extract_to=args.extract_to),ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print('DOWNLOAD CHECK FAILED: '+str(exc),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())

"""Build an independently recoverable cumulative release from a CLEAN Git tree.

Publication happens only after a fresh ZIP extraction, full hash check, wheel
comparison, bundle fsck, stage ancestry and baseline patch replay all pass.
No release flag is promoted; platform/runtime tests remain separate evidence.
"""
from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile

FONT_EXTS = {'.ttf', '.otf', '.ttc', '.woff', '.woff2'}


def run(args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, check=True,
                            timeout=180, text=True, encoding='utf-8', errors='replace')
    return result.stdout


def publish_new(source: Path, destination: Path) -> None:
    """Copy exclusively; never unlink a pre-existing/racing destination."""
    owned = False
    try:
        with destination.open('xb') as dst:
            owned = True
            with source.open('rb') as src:
                shutil.copyfileobj(src, dst)
    except BaseException:
        if owned:
            destination.unlink(missing_ok=True)
        raise


def build(repo: Path, output: Path, evidence: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise FileExistsError('Existing downloads are never overwritten')
    receipt_path = output.with_suffix('.receipt.json')
    if receipt_path.exists():
        raise FileExistsError('Existing receipt is never overwritten')
    def git(*args):
        return run(['git', '-c', 'core.fsmonitor=false', *args], cwd=repo)
    if git('status', '--porcelain').strip():
        raise ValueError('Commit source and documentation before packaging')
    version = json.loads((repo/'DELIVERY_VERSION.json').read_text(encoding='utf-8'))
    stage = version['stage']
    if stage not in {'R8','R9','R10'}:
        raise ValueError('Unsupported cumulative stage')
    head, tree = git('rev-parse','HEAD').strip(), git('rev-parse','HEAD^{tree}').strip()
    names = [n for n in git('ls-files','-z').split('\0') if n]
    if any(Path(n).suffix.lower() in FONT_EXTS for n in names):
        raise ValueError('Do not distribute font files')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='manju-release-',dir=output.parent) as td:
        temp=Path(td);root=temp/f'MANJU_{stage}_CUMULATIVE';source=root/'source';source.mkdir(parents=True)
        for name in names:
            original=repo/name
            if original.is_symlink() or not original.is_file():
                raise ValueError('Only regular source files are supported')
            target=source/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,target)
        git('bundle','create',str(root/'repository.bundle'),'--all')
        diff=subprocess.check_output(['git','diff','--binary',version['baseline'],'HEAD'],cwd=repo)
        (root/'CHANGES_FROM_R2.patch').write_bytes(diff)
        wheels=root/'wheels';wheels.mkdir()
        wheel_log=run([sys.executable,'-m','pip','wheel','--no-deps','--no-build-isolation',
                       '--disable-pip-version-check','--wheel-dir',str(wheels),str(source)])
        # Build artifacts belong outside the immutable tracked source copy.
        for extra in ['build','src/manju.egg-info']:
            shutil.rmtree(source/extra,ignore_errors=True)
        wheel=next(wheels.glob('*.whl'))
        for name in ['VERIFY_PACKAGE.py','ZIP_CHECK.py','LAUNCH.py','INSTALL_WINDOWS.cmd',
                     'START_WINDOWS.cmd','VERIFY_WINDOWS.cmd','QUICKSTART_ZH.md']:
            shutil.copy2(repo/'tools/delivery'/name,root/name)
        shutil.copy2(repo/'tools/model_workbench.html',root/'OPEN_MODEL_WORKBENCH.html')
        intro=(repo/'tools/delivery/QUICKSTART_ZH.md').read_text(encoding='utf-8').replace('R7',stage)
        intro += ('\n\n## 本轮工作现场与模型回收\n'
                  '完整工作现场 ZIP 会保存实际素材、能力档、未完成文字与审片记录。恢复须显式确认，当前批准勾选会清除。\n'
                  '所有商业模型仍为离线作者适配，不直接调用生成 API，不授权付款或自动选片。\n'
                  '请同时保存完整 ZIP 与校验收据；校验结果不能代替浏览器下载列表中的保存确认。\n')
        (root/'QUICKSTART_ZH.md').write_text(intro,encoding='utf-8')
        (root/'START_HERE.md').write_text(f'# Manju {stage} 累计修复候选\n\n'
            '完整解压到新目录，先打开 OPEN_MODEL_WORKBENCH.html。完整应用见 QUICKSTART_ZH.md。\n\n'
            '历史 R3/R4 是功能重建，不是原始丢失 ZIP 的字节复原。不要覆盖旧目录和真实影片工程。\n\n'
            '源码、wheel、完整 Git 历史和实际测试记录在此包中。先运行 VERIFY_PACKAGE.py；带 --git 可复原历史并重放补丁。\n'
            '本包没有通过 Windows 正式发布矩阵，没有商用生成实测，也不包含 Python/FFmpeg/第三方依赖。\n',encoding='utf-8')
        ev=root/'EVIDENCE';ev.mkdir()
        for original in sorted(evidence.rglob('*')):
            if original.is_file() and original.suffix.lower() in {'.json','.xml','.log','.md','.txt','.rc','.png','.mp4'}:
                if original.stat().st_size>32*1024*1024:raise ValueError('Evidence file unexpectedly large')
                dst=ev/original.relative_to(evidence);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,dst)
        (ev/'wheel-build.log').write_text(wheel_log,encoding='utf-8')
        meta={**version,'git_head':head,'git_tree':tree,'wheel':'wheels/'+wheel.name,
              'created_at':datetime.now(timezone.utc).isoformat(),
              'prior_verified_stage':'R7','prior_archive_sha256':'eec2efcf3e56c31dab55a8cd4a60fdf3afba0887bf4da9b3b06022e6103060bc'}
        (root/'PACKAGE.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        sums={p.relative_to(root).as_posix():sha256(p.read_bytes()).hexdigest()
              for p in sorted(root.rglob('*')) if p.is_file()}
        (root/'SHA256SUMS.json').write_text(json.dumps(sums,indent=2)+'\n',encoding='utf-8')
        first=json.loads(run([sys.executable,str(root/'VERIFY_PACKAGE.py'),'--git']))
        archive=temp/'verified.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for p in sorted(root.rglob('*')):
                if p.is_file():z.write(p,p.relative_to(temp).as_posix())
        check=temp/'fresh';check.mkdir()
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise ValueError('ZIP CRC mismatch')
            if len(z.namelist())!=len(set(z.namelist())):raise ValueError('Duplicate ZIP entry')
            z.extractall(check)
        fresh=check/root.name
        verified=json.loads(run([sys.executable,str(fresh/'VERIFY_PACKAGE.py'),'--git']))
        if not verified['ok'] or first!=verified:raise ValueError('Fresh restore differs')
        result={**verified,'archive':str(output),'archive_bytes':archive.stat().st_size,
                'archive_sha256':sha256(archive.read_bytes()).hexdigest(),
                'zip_crc_passed':True,'fresh_extraction_checked':True,
                'formal_windows_release':False,'client_download_confirmed':False,
                'base_r7_is_ancestor':True,'tests_are_separate_evidence':True}
        publish_new(archive, output)
        if sha256(output.read_bytes()).hexdigest()!=result['archive_sha256']:
            raise ValueError('Published archive differs from verified file')
        with receipt_path.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2)
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path);parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    args=parser.parse_args();print(json.dumps(build(args.repo.resolve(),args.output.resolve(),args.evidence.resolve()),indent=2))

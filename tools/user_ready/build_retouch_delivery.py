"""Publish real, verified continuation downloads from a clean committed tree.

Builds the cumulative source/wheel/history core, full wrapper, starter, five
checked transport fragments, and a content-bound offline download helper.
All destinations are new files. Only stdlib + the existing wheel build toolchain.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile


def module(path, name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def describe(path):return {'name':path.name,'bytes':path.stat().st_size,'sha256':sha(path)}


def verify_zip(path):
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        if len(set(names))!=len(names):raise ValueError('Duplicate ZIP entry')
        for name in names:
            p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name:raise ValueError('Unsafe ZIP path')
            if p.suffix.lower() in {'.ttf','.otf','.ttc','.woff','.woff2'}:raise ValueError('Do not distribute fonts')
        if z.testzip() is not None:raise ValueError('ZIP CRC failure')


def zip_tree(root, output):
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(root.rglob('*')):
            if p.is_symlink():raise ValueError('No linked release file')
            if p.is_file():z.write(p,p.relative_to(root.parent).as_posix())
    verify_zip(output)


def common(repo, root, example):
    root.mkdir(parents=True,exist_ok=True)
    (root/'APP').mkdir(exist_ok=True);(root/'EXAMPLES').mkdir(exist_ok=True)
    shutil.copy2(repo/'tools/user_ready/START_HERE.html',root/'START_HERE.html')
    shutil.copy2(repo/'tools/user_ready/CONTINUE_README.md',root/'READ_FIRST.md')
    shutil.copy2(repo/'tools/user_ready/VERIFY_DELIVERY.py',root/'VERIFY_DELIVERY.py')
    for name in ['LOCAL_WORKBENCH.py','LOCAL_CONTINUE.py','CONTINUE_CLIENT.js',
                 'OPEN_LOCAL_WINDOWS.cmd','OPEN_CONTINUE_WINDOWS.cmd']:
        shutil.copy2(repo/'tools/delivery'/name,root/name)
    shutil.copy2(repo/'tools/model_workbench.html',root/'APP/OPEN_MODEL_WORKBENCH.html')
    shutil.copy2(example,root/'EXAMPLES/CHECKOUT.zip')
    for manifest,names in [('LOCAL_WORKBENCH_FILES.json',['START_HERE.html','APP/OPEN_MODEL_WORKBENCH.html','EXAMPLES/CHECKOUT.zip']),
                           ('CONTINUE_FILES.json',['CONTINUE_CLIENT.js'])]:
        (root/manifest).write_text(json.dumps({n:sha(root/n) for n in names},indent=2),encoding='utf-8')


def inventory(root, version, head):
    value={'schema':'manju.download-delivery/v1','runtime_stage':'R16','version':version,'git_head':head,
           'files':{p.relative_to(root).as_posix():{'bytes':p.stat().st_size,'sha256':sha(p)}
                    for p in sorted(root.rglob('*')) if p.is_file()}}
    (root/'DELIVERY_MANIFEST.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
    checker=module(root/'VERIFY_DELIVERY.py','check_outer')
    result=checker.verify(root)
    if not result['ok']:raise ValueError(result)
    return result


def evidence_gate(repo, evidence):
    """Require completed selected tests and actual source/installed retouch runs."""
    import xml.etree.ElementTree as ET
    if int((evidence/'regression.rc').read_text(encoding='utf-8').strip()) != 0:
        raise ValueError('Selected regression has no successful completion record')
    suites=ET.parse(evidence/'regression.xml').getroot()
    cases=list(suites.iter('testcase'))
    if not cases or any(c.find('failure') is not None or c.find('error') is not None for c in cases):
        raise ValueError('Selected regression report is missing or failing')
    expected=json.loads((evidence/'tested-source-hashes.json').read_text(encoding='utf-8'))
    if not expected or any(sha(repo/name)!=value for name,value in expected.items()):
        raise ValueError('Source changed after terminal tests')
    a=json.loads((evidence/'source-retouch/RESULT.json').read_text(encoding='utf-8'))
    b=json.loads((evidence/'installed-retouch/RESULT.json').read_text(encoding='utf-8'))
    parity=json.loads((evidence/'installed-parity.json').read_text(encoding='utf-8'))
    if not a.get('ok') or not b.get('ok') or not parity.get('ok'):
        raise ValueError('Missing actual source/installed media rehearsal')
    for key in ('kit_sha256','director_after_sha256','checkout_after_sha256'):
        if a[key]!=b[key] or parity[key]!=a[key]:
            raise ValueError('Source and installed retouch outputs differ')
    return {'tests':len(cases),'skipped':sum(c.find('skipped') is not None for c in cases),
            'failures':0,'runtime_files':parity['runtime_files']}


def add_example(root, evidence):
    target=root/'EXAMPLES/RETOUCH';target.mkdir(parents=True)
    source=evidence/'source-retouch'
    for name in ['DIRECTOR_BEFORE.zip','TASK.json','RETOUCH_KIT.zip']:
        shutil.copy2(source/name,target/name)
    for p in sorted(source.glob('A*_TARGET.png')):shutil.copy2(p,target/p.name)
    (target/'READ_ME.txt').write_text(
        '这是实际合成测试素材，不是商业模型生成。先进入工作台第07区，核验并恢复 DIRECTOR_BEFORE.zip（不要解压此文件）。\n'
        '在同区精修回传选择 TASK.json 与 A01/A02 开头的 PNG，可分批选择；预览后明确确认绑定为未批准目标图。\n'
        'RETOUCH_KIT.zip 是导出给外部工具的示例，可解压查看逐张提示词和参考图；不含完整原视频，不是备份。\n'
        '改完另存导演包或首页收工包。没有自动生成、上传、计费、选片或锁片。\n',encoding='utf-8')


def build(repo, destination, evidence, example):
    if not destination.is_dir() or any(destination.iterdir()):
        raise ValueError('Use a new empty output directory; existing downloads are never replaced')
    selected=evidence_gate(repo,evidence)
    core=module(repo/'tools/rebuilt-delivery/build_cumulative.py','cumulative_delivery')
    fragment=module(repo/'tools/user_ready/download_parts.py','fragment_delivery')
    version=json.loads((repo/'DELIVERY_VERSION.json').read_text(encoding='utf-8'))['version']
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    with tempfile.TemporaryDirectory(prefix='manju-final-build-',dir=destination.parent) as td:
        temp=Path(td);raw=temp/'core.zip';core_receipt=core.build(repo,raw,evidence,maintenance_reports=repo/'REPORTS/retouch')
        core_root=temp/'core-unpacked'
        with zipfile.ZipFile(raw) as z:z.extractall(core_root)
        app=next(core_root.iterdir())
        full_root=temp/'MANJU_CURRENT';full_root.mkdir();shutil.copytree(app,full_root/'APP')
        common(repo,full_root,example)
        add_example(full_root,evidence)
        shutil.copy2(repo/'tools/user_ready/DIAGNOSE_WINDOWS.cmd',full_root/'DIAGNOSE_WINDOWS.cmd')
        (full_root/'HANDOFF').mkdir()
        latest=max(repo.glob('PROJECT_STATE_*.md'),key=lambda p:p.name)
        shutil.copy2(latest,full_root/'HANDOFF'/latest.name)
        for source in repo.glob('TASK_关键帧精修_*.md'):shutil.copy2(source,full_root/'HANDOFF'/source.name)
        for filename in ['VALIDATION.md','PROJECT_REVIEW.md','RESEARCH.md']:
            shutil.copy2(repo/'REPORTS/retouch'/filename,full_root/filename)
        full_check=inventory(full_root,version,head)
        full=temp/'MANJU_CURRENT_FULL.zip';zip_tree(full_root,full)
        start_root=temp/'MANJU_CURRENT_START';common(repo,start_root,example)
        add_example(start_root,evidence)
        start_check=inventory(start_root,version,head)
        start=temp/'MANJU_CURRENT_START.zip';zip_tree(start_root,start)
        if (start_root/'APP/OPEN_MODEL_WORKBENCH.html').read_bytes()!=(full_root/'APP/OPEN_MODEL_WORKBENCH.html').read_bytes():
            raise ValueError('Starter page mismatch')
        fresh=temp/'fresh';fresh.mkdir()
        with zipfile.ZipFile(full) as z:z.extractall(fresh)
        checked=module(fresh/'MANJU_CURRENT/VERIFY_DELIVERY.py','fresh_outer').verify(fresh/'MANJU_CURRENT')
        if checked!=full_check:raise ValueError('Fresh wrapper mismatch')
        # Recheck the wheel/source/history core after final outer compression.
        restored=json.loads(subprocess.check_output([sys.executable,str(fresh/'MANJU_CURRENT/APP/VERIFY_PACKAGE.py'),'--git'],text=True,timeout=180))
        if not restored['ok']:raise ValueError('Core recovery failed in final wrapper')
        parts_dir=temp/'parts'
        parts=fragment.split_archive(full,parts_dir,block_size=(full.stat().st_size+4)//5)
        if len(parts['parts'])!=5:raise ValueError('Expected five fragments')
        for item in parts['parts']:
            old=parts_dir/item['name'];new=parts_dir/('MANJU_CURRENT_PART_'+str(item['index']).zfill(2)+'.zip')
            old.rename(new);item['name']=new.name
        paths=[parts_dir/p['name'] for p in parts['parts']]
        fragment_check=fragment.reassemble(list(reversed(paths)),temp/'reassembled.zip',parts)
        if sha(temp/'reassembled.zip')!=sha(full):raise ValueError('Transport fragments changed content')
        release={'files':[describe(full),describe(start)],'parts':parts}
        html=(repo/'tools/user_ready/DOWNLOAD_HELPER.template.html').read_text(encoding='utf-8')
        html=html.replace('__SHA256__',(repo/'tools/user_ready/sha256.js').read_text(encoding='utf-8')).replace('__RELEASE__',json.dumps(release,ensure_ascii=True))
        helper=temp/'MANJU_CURRENT_DOWNLOAD_HELPER.html';helper.write_text(html,encoding='utf-8')
        helper_root=temp/'MANJU_DOWNLOAD_HELPER';helper_root.mkdir();shutil.copy2(helper,helper_root/'OPEN_DOWNLOAD_HELPER.html')
        (helper_root/'README.txt').write_text('完整解压，打开 OPEN_DOWNLOAD_HELPER.html。选择完整包核验，或选择本轮五个分段 ZIP 合成。分段不用解压，原文件不修改。\n',encoding='utf-8')
        helper_zip=temp/'MANJU_CURRENT_DOWNLOAD_HELPER.zip';zip_tree(helper_root,helper_zip)
        demo=temp/'MANJU_CURRENT_RETOUCH_EXAMPLE.zip';zip_tree(full_root/'EXAMPLES/RETOUCH',demo)
        published=[full,start,helper,helper_zip,demo,*paths]
        for path in published:
            if path.suffix=='.zip':verify_zip(path)
            core.publish_new(path,destination/path.name)
            if sha(destination/path.name)!=sha(path):raise ValueError('Publication changed bytes')
        result={'version':version,'git_head':head,'files':[describe(destination/p.name) for p in published],
                'selected_regression':selected,'core':core_receipt,'outer':full_check,'starter':start_check,'fresh_core':restored,
                'fragments_reassembled':fragment_check,'publication_integrity_checked':True,
                'native_windows_acceptance':False,'client_download_confirmed':False}
        (destination/'MANJU_CURRENT_VERIFIED.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        (destination/'MANJU_CURRENT_SHA256.txt').write_text('\n'.join(p['sha256']+'  '+p['name'] for p in result['files'])+'\n',encoding='utf-8')
        (destination/'PARTS.json').write_text(json.dumps(parts,indent=2),encoding='utf-8')
        return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--output',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--example',type=Path,required=True)
    a=p.parse_args();print(json.dumps(build(a.repo.absolute(),a.output.absolute(),a.evidence.absolute(),a.example.absolute()),indent=2))

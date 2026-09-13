"""Publish the verified IDE/comfortable-writing maintenance release.

The package gate requires completed tests, exact runtime/installed parity and
actual file/CLI/browser cycles. It never treats a screenshot as an implementation.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

PREFIX='MANJU_IDE'
VERSION='0.2.0+r16.9'


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def evidence_gate(repo,evidence):
    sha=load(repo/'tools/user_ready/build_experience_delivery.py','ide_gate_support').sha
    if int((evidence/'regression.rc').read_text(encoding='utf-8'))!=0:
        raise ValueError('Terminal selected regression did not succeed')
    cases=list(ET.parse(evidence/'regression.xml').getroot().iter('testcase'))
    if not cases or any(c.find('failure') is not None or c.find('error') is not None for c in cases):
        raise ValueError('Empty or failing selected regression')
    identities=[(c.get('classname'),c.get('name')) for c in cases]
    if len(identities)!=len(set(identities)):raise ValueError('Duplicate test identities')
    frozen=json.loads((evidence/'tested-source-hashes.json').read_text(encoding='utf-8'))
    if not frozen or any(sha(repo/n)!=h for n,h in frozen.items()):
        raise ValueError('Source changed after terminal tests')
    reports=[json.loads((evidence/p/'RESULT.json').read_text(encoding='utf-8')) for p in ('source-ide-complete','installed-ide')]
    required=('ok','actual_browser_download','actual_cli_roundtrip','new_story_and_text_restored','old_briefs_immutable',
              'old_briefs_show_stale','source_backup_unchanged','fresh_checkout_reopen_identical','legacy_checkout_reexport_identical')
    for r in reports:
        if not all(r.get(k) is True for k in required) or r.get('javascript_errors') or r.get('network_requests'):
            raise ValueError('Actual source/installed rehearsal missing or failing')
        if r['html_sha256']!=sha(repo/'tools/model_workbench.html'):
            raise ValueError('Rehearsal used different HTML bytes')
    a,b=reports
    for key in ('version','input_sha256','media_sha256','unchanged_media_files','browser_checkout_sha256','high_resolution_png_preserved'):
        if a[key]!=b[key]:raise ValueError('Rehearsal parity mismatch: '+key)
    if a['runtime']==b['runtime']:raise ValueError('Installed rehearsal must be independent')
    parity=json.loads((evidence/'installed-parity.json').read_text(encoding='utf-8'))
    if parity.get('ok') is not True or not parity.get('runtime_hashes'):
        raise ValueError('Independent runtime parity missing')
    if parity['runtime_hashes']!={n:sha(repo/'src'/n) for n in parity['runtime_hashes']}:
        raise ValueError('Installed runtime no longer matches source')
    return {'tests':len(cases),'passed':sum(c.find('skipped') is None for c in cases),
            'skipped':sum(c.find('skipped') is not None for c in cases),'failures':0,
            'runtime_files':parity['files_checked'],'source_files_frozen':len(frozen)}


def ide_entry(root:Path):
    """Full-package entry. No auto-run tasks, extensions, install or credentials."""
    (root/'AGENTS.md').write_text('# Manju 完整交付根目录\n\n实际应用源码在 APP/source/。先读 APP/source/AGENTS.md，再执行只读自检：\n\n    python APP/source/tools/ai_bootstrap.py --json\n\n按返回的路径读取最新状态。APP/source 没有 .git；完整历史在 APP/repository.bundle。要维护代码时将 bundle 克隆到新目录，不在原目录初始化空仓库。用户作品放 WORKS/ 或其指定位置，不改原包或实际原媒体。\n网页未保存草稿无法从磁盘读取；先保存收工包，按 APP/source/docs/ai/OPERATIONS.md 的 IDE 往返路线操作。\n安装、付费生成、上传、选片和锁片仍需原有明确授权。此入口不是权限沙箱。\n',encoding='utf-8')
    (root/'CLAUDE.md').write_text('@AGENTS.md\n',encoding='utf-8')
    (root/'WORKS').mkdir()
    (root/'WORKS/README.md').write_text('# 你的作品与工作副本\n\n可在这里放已保存收工ZIP、IDE工作副本，或把IDE指向你已有作品目录。软件源码不是影片工程。这个目录不是自动备份，不会默认上传。解压新版使用新目录，作品独立备份。\n',encoding='utf-8')
    workspace={'folders':[{'name':'Manju','path':'APP/source'},{'name':'Works','path':'WORKS'}],
               'tasks':{'version':'2.0.0','tasks':[{'label':'Manju：只读 AI 接管自检','type':'process','command':'python',
                'args':['tools/ai_bootstrap.py','--json'],'options':{'cwd':'${workspaceFolder:Manju}'},'problemMatcher':[]}]}}
    (root/'MANJU.code-workspace').write_text(json.dumps(workspace,ensure_ascii=False,indent=2),encoding='utf-8')


def build(repo,destination,evidence,example):
    if not destination.is_dir() or any(destination.iterdir()):raise ValueError('Use a new empty destination')
    selected=evidence_gate(repo,evidence)
    common=load(repo/'tools/user_ready/build_experience_delivery.py','ide_common')
    core=load(repo/'tools/rebuilt-delivery/build_cumulative.py','ide_core')
    fragment=load(repo/'tools/user_ready/download_parts.py','ide_fragments')
    version=json.loads((repo/'DELIVERY_VERSION.json').read_text(encoding='utf-8'))['version']
    if version!=VERSION:raise ValueError('Unexpected release version')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    def examples(root):
        target=root/'EXAMPLES/IDE';target.mkdir(parents=True)
        for n in ('CHECKOUT_FOR_AI.zip','RETURNED_CHECKOUT.zip','IDE_PREVIEW.json'):
            shutil.copy2(evidence/'source-ide-complete'/n,target/n)
        story=root/'EXAMPLES/STORY';story.mkdir()
        shutil.copy2(example,story/'STORY_CHECKOUT.zip')
        (target/'READ_ME.txt').write_text('这是脚本模拟IDE编辑的虚构故事与合成素材，不是实际Codex/Claude调用。\n在首页打开 CHECKOUT_FOR_AI.zip 看原稿，或打开 RETURNED_CHECKOUT.zip 看AI工作副本的候选结果；均要核验预览确认，ZIP不用解压。新结果保留旧简报，旧引用显示需要复核。\n要自己操作，安装本轮CLI后按 AI_START_HERE.md 或完整包APP/source/docs/ai/OPERATIONS.md执行。不能覆盖真实作品或把后来的网页新稿自动合并。\n',encoding='utf-8')
        for n in ('README.md','VALIDATION.md','PROJECT_REVIEW.md'):
            shutil.copy2(repo/'REPORTS/ide_ux'/n,root/n)
        shutil.copy2(repo/'REPORTS/ide_ux/README.md',root/'READ_FIRST.md')
        shutil.copy2(repo/'docs/ai/OPERATIONS.md',root/'AI_START_HERE.md')
    with tempfile.TemporaryDirectory(prefix='manju-ide-publish-',dir=destination.parent) as td:
        temp=Path(td);raw=temp/'core.zip';receipt=core.build(repo,raw,evidence,maintenance_reports=repo/'REPORTS/ide_ux')
        with zipfile.ZipFile(raw) as z:z.extractall(temp/'core')
        app=next((temp/'core').iterdir());fullroot=temp/PREFIX;fullroot.mkdir();shutil.copytree(app,fullroot/'APP')
        common.common(repo,fullroot,example);examples(fullroot);ide_entry(fullroot)
        shutil.copy2(repo/'tools/user_ready/DIAGNOSE_WINDOWS.cmd',fullroot/'DIAGNOSE_WINDOWS.cmd')
        (fullroot/'HANDOFF').mkdir()
        latest=max(repo.glob('PROJECT_STATE_*.md'),key=lambda p:p.name);shutil.copy2(latest,fullroot/'HANDOFF'/latest.name)
        for p in repo.glob('TASK_IDE*.md'):shutil.copy2(p,fullroot/'HANDOFF'/p.name)
        fullcheck=common.inventory(fullroot,version,head);full=temp/(PREFIX+'_FULL.zip');common.zip_tree(fullroot,full)
        startroot=temp/(PREFIX+'_START');common.common(repo,startroot,example);examples(startroot)
        (startroot/'NO_SOURCE_HERE.txt').write_text('这是免安装网页上手包，不包含源码、Git历史或CLI。用本地IDE接管完整软件请取 MANJU_IDE_FULL.zip。\n',encoding='utf-8')
        startcheck=common.inventory(startroot,version,head);start=temp/(PREFIX+'_START.zip');common.zip_tree(startroot,start)
        assert (fullroot/'APP/OPEN_MODEL_WORKBENCH.html').read_bytes()==(startroot/'APP/OPEN_MODEL_WORKBENCH.html').read_bytes()
        with zipfile.ZipFile(full) as z:z.extractall(temp/'fresh')
        fresh=temp/'fresh'/PREFIX
        restored=load(fresh/'VERIFY_DELIVERY.py','ide_fresh').verify(fresh)
        if restored!=fullcheck:raise ValueError('Fresh wrapper differs')
        recovered=json.loads(subprocess.check_output([sys.executable,str(fresh/'APP/VERIFY_PACKAGE.py'),'--git'],text=True,timeout=180))
        if not recovered['ok']:raise ValueError('Final core recovery failed')
        # Prove the cold-entry checker works from another cwd in the actual source snapshot.
        boot=json.loads(subprocess.check_output([sys.executable,str(fresh/'APP/source/tools/ai_bootstrap.py'),'--json'],cwd=temp,text=True,timeout=30))
        if not boot['ok'] or boot['git_worktree_present'] or not boot['adjacent_history_bundle_present']:
            raise ValueError('Extracted-source cold bootstrap differs from advertised layout')
        parts=fragment.split_archive(full,temp/'parts',block_size=(full.stat().st_size+4)//5)
        if len(parts['parts'])!=5:raise ValueError('Unexpected fragment count')
        for item in parts['parts']:
            name=PREFIX+'_PART_'+str(item['index']).zfill(2)+'.zip';(temp/'parts'/item['name']).rename(temp/'parts'/name);item['name']=name
        paths=[temp/'parts'/x['name'] for x in parts['parts']]
        joined=fragment.reassemble(paths[::-1],temp/'reassembled.zip',parts)
        if common.sha(temp/'reassembled.zip')!=common.sha(full):raise ValueError('Fragment content differs')
        release={'files':[common.describe(full),common.describe(start)],'parts':parts}
        html=(repo/'tools/user_ready/DOWNLOAD_HELPER.template.html').read_text(encoding='utf-8').replace('__SHA256__',(repo/'tools/user_ready/sha256.js').read_text(encoding='utf-8')).replace('__RELEASE__',json.dumps(release,ensure_ascii=True))
        helper=temp/(PREFIX+'_DOWNLOAD_HELPER.html');helper.write_text(html,encoding='utf-8')
        hroot=temp/'MANJU_DOWNLOAD_HELPER';hroot.mkdir();shutil.copy2(helper,hroot/'OPEN_DOWNLOAD_HELPER.html')
        (hroot/'README.txt').write_text('解压后打开 OPEN_DOWNLOAD_HELPER.html，选回完整或上手 ZIP 核验；也可一次选择本轮五个分段ZIP，不解压分段、不上传。\n',encoding='utf-8')
        helpzip=temp/(PREFIX+'_DOWNLOAD_HELPER.zip');common.zip_tree(hroot,helpzip)
        published=[full,start,helper,helpzip,*paths]
        for src in published:
            if src.suffix=='.zip':common.verify_zip(src)
            core.publish_new(src,destination/src.name)
            if common.sha(destination/src.name)!=common.sha(src):raise ValueError('Published bytes changed')
        result={'version':version,'git_head':head,'files':[common.describe(destination/p.name) for p in published],
                'selected_regression':selected,'core':receipt,'outer':fullcheck,'starter':startcheck,'fresh_core':recovered,
                'cold_bootstrap':boot,'fragments_reassembled':joined,'publication_integrity_checked':True,
                'native_windows_acceptance':False,'commercial_ide_agent_tested':False,'client_download_confirmed':False}
        (destination/(PREFIX+'_VERIFIED.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        (destination/(PREFIX+'_SHA256.txt')).write_text('\n'.join(x['sha256']+'  '+x['name'] for x in result['files'])+'\n',encoding='utf-8')
        (destination/'PARTS.json').write_text(json.dumps(parts,indent=2),encoding='utf-8');return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--output',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--example',type=Path,required=True)
    a=p.parse_args();print(json.dumps(build(a.repo.resolve(),a.output.resolve(),a.evidence.resolve(),a.example.resolve()),ensure_ascii=False,indent=2))

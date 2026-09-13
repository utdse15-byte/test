"""Publish only completed, tested story integration plus its unchanged legacy core.

No inherited claim from old UX/retouch gates. Real source + installed story
rehearsals, terminal test XML/rc and source fingerprints own this release gate.
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


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def evidence_gate(repo,evidence):
    sha=load(repo/'tools/user_ready/build_experience_delivery.py','story_support_gate').sha
    if int((evidence/'regression.rc').read_text())!=0:
        raise ValueError('Terminal selected regression did not complete successfully')
    cases=list(ET.parse(evidence/'regression.xml').getroot().iter('testcase'))
    if not cases or any(c.find('failure') is not None or c.find('error') is not None for c in cases):
        raise ValueError('Selected regression is empty or failing')
    frozen=json.loads((evidence/'tested-source-hashes.json').read_text())
    if not frozen or any(sha(repo/name)!=expected for name,expected in frozen.items()):
        raise ValueError('Runtime or test source changed after terminal checks')
    reports=[json.loads((evidence/part/'RESULT.json').read_text()) for part in ('source-story','installed-story')]
    required=('ok','actual_browser_download','story_scope_exclusion','stale_scene_blocks_link','old_brief_not_overwritten','fresh_checkout_reopen_identical','legacy_inner_reexport_identical')
    for r in reports:
        if not all(r.get(k) is True for k in required) or r['javascript_errors'] or r['network_requests']:
            raise ValueError('Actual story rehearsal missing or failed')
        if r['html_sha256']!=sha(repo/'tools/model_workbench.html'):
            raise ValueError('Rehearsal used different page bytes')
    a,b=reports
    for key in ('version','input_sha256','media_sha256','brief_content_hashes','unchanged_media_files','high_resolution_png_preserved'):
        if a[key]!=b[key]:raise ValueError('Installed/story evidence semantics differ: '+key)
    if a['runtime']==b['runtime']:raise ValueError('Installed rehearsal must use an independent installation')
    parity=json.loads((evidence/'installed-parity.json').read_text())
    if parity.get('ok') is not True or parity.get('files_checked',0)<1:
        raise ValueError('Independent runtime parity missing')
    if parity['runtime_hashes']!={name:sha(repo/'src'/name) for name in parity['runtime_hashes']}:
        raise ValueError('Installed runtime no longer matches source')
    return {'tests':len(cases),'passed':sum(c.find('skipped') is None for c in cases),
            'skipped':sum(c.find('skipped') is not None for c in cases),'failures':0,
            'runtime_files':parity['files_checked'],'source_files_frozen':len(frozen),
            'note':'Each real save/reopen is byte-identical; independent newly-created brief IDs are intentionally random.'}


def build(repo, destination,evidence,example):
    if not destination.is_dir() or any(destination.iterdir()):raise ValueError('Use a new empty destination')
    selected=evidence_gate(repo,evidence)
    common=load(repo/'tools/user_ready/build_experience_delivery.py','story_common')
    core=load(repo/'tools/rebuilt-delivery/build_cumulative.py','story_cumulative')
    fragment=load(repo/'tools/user_ready/download_parts.py','story_parts')
    version=json.loads((repo/'DELIVERY_VERSION.json').read_text())['version']
    if version!='0.2.0+r16.8':raise ValueError('Unexpected explicit story release version')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    prefix='MANJU_STORY'
    def examples(root):
        target=root/'EXAMPLES/STORY';target.mkdir(parents=True)
        for name in ['STORY.json','STORY_BRIEF.zip','STORY_CHECKOUT.zip']:
            shutil.copy2(evidence/'source-story'/name,target/name)
        (target/'READ_ME.txt').write_text('这是原创虚构故事与真实合成媒体，不是商业模型画质样本。\n最快：在首页打开本目录 STORY_CHECKOUT.zip（不用解压），核验预览并确认；进入镜头与素材。旧简报保留按住箱子的动作，新简报是主动交出，旧版显示待复核。\n从头试：先打开上一级 CHECKOUT.zip，然后在镜头与素材中导入 STORY.json，预览确认；在版本区下载简报或明确带入当前镜头。\n改动作后旧简报需复核，不会自动重生成。改完用顶部保存收工包并选回核验。故事JSON和外发简报都不是整部影片工程备份。\n',encoding='utf-8')
        shutil.copy2(repo/'REPORTS/story/README.md',root/'READ_FIRST.md')
    with tempfile.TemporaryDirectory(prefix='manju-story-publish-',dir=destination.parent) as td:
        temp=Path(td);raw=temp/'core.zip';core_receipt=core.build(repo,raw,evidence,maintenance_reports=repo/'REPORTS/story')
        with zipfile.ZipFile(raw) as z:z.extractall(temp/'core')
        app=next((temp/'core').iterdir());fullroot=temp/prefix;fullroot.mkdir();shutil.copytree(app,fullroot/'APP')
        common.common(repo,fullroot,example);examples(fullroot)
        shutil.copy2(repo/'tools/user_ready/DIAGNOSE_WINDOWS.cmd',fullroot/'DIAGNOSE_WINDOWS.cmd')
        (fullroot/'HANDOFF').mkdir()
        latest=max(repo.glob('PROJECT_STATE_*.md'),key=lambda p:p.name);shutil.copy2(latest,fullroot/'HANDOFF'/latest.name)
        for p in repo.glob('TASK_故事制作闭环_*.md'):shutil.copy2(p,fullroot/'HANDOFF'/p.name)
        for n in ['README.md','VALIDATION.md','PROJECT_REVIEW.md']:shutil.copy2(repo/'REPORTS/story'/n,fullroot/n)
        fullcheck=common.inventory(fullroot,version,head);full=temp/(prefix+'_FULL.zip');common.zip_tree(fullroot,full)
        startroot=temp/(prefix+'_START');common.common(repo,startroot,example);examples(startroot)
        startcheck=common.inventory(startroot,version,head);start=temp/(prefix+'_START.zip');common.zip_tree(startroot,start)
        assert (fullroot/'APP/OPEN_MODEL_WORKBENCH.html').read_bytes()==(startroot/'APP/OPEN_MODEL_WORKBENCH.html').read_bytes()
        with zipfile.ZipFile(full) as z:z.extractall(temp/'fresh')
        fresh=temp/'fresh'/prefix
        restored=load(fresh/'VERIFY_DELIVERY.py','story_fresh').verify(fresh)
        if restored!=fullcheck:raise ValueError('Fresh wrapper differs')
        recovered=json.loads(subprocess.check_output([sys.executable,str(fresh/'APP/VERIFY_PACKAGE.py'),'--git'],text=True,timeout=180))
        if not recovered['ok']:raise ValueError('Final core recovery failed')
        parts=fragment.split_archive(full,temp/'parts',block_size=(full.stat().st_size+4)//5)
        if len(parts['parts'])!=5:raise ValueError('Unexpected fragment count')
        for item in parts['parts']:
            name=prefix+'_PART_'+str(item['index']).zfill(2)+'.zip';(temp/'parts'/item['name']).rename(temp/'parts'/name);item['name']=name
        paths=[temp/'parts'/p['name'] for p in parts['parts']]
        joined=fragment.reassemble(paths[::-1],temp/'reassembled.zip',parts)
        if common.sha(temp/'reassembled.zip')!=common.sha(full):raise ValueError('Fragment content differs')
        release={'files':[common.describe(full),common.describe(start)],'parts':parts}
        html=(repo/'tools/user_ready/DOWNLOAD_HELPER.template.html').read_text().replace('__SHA256__',(repo/'tools/user_ready/sha256.js').read_text()).replace('__RELEASE__',json.dumps(release,ensure_ascii=True))
        helper=temp/(prefix+'_DOWNLOAD_HELPER.html');helper.write_text(html,encoding='utf-8')
        helproot=temp/'MANJU_DOWNLOAD_HELPER';helproot.mkdir();shutil.copy2(helper,helproot/'OPEN_DOWNLOAD_HELPER.html')
        (helproot/'README.txt').write_text('完整解压后打开 OPEN_DOWNLOAD_HELPER.html；选择完整或上手ZIP核验。也可一次选择本轮五个分段ZIP合成，不解压分段，不上传文件。\n',encoding='utf-8')
        helpzip=temp/(prefix+'_DOWNLOAD_HELPER.zip');common.zip_tree(helproot,helpzip)
        published=[full,start,helper,helpzip,*paths]
        for src in published:
            if src.suffix=='.zip':common.verify_zip(src)
            core.publish_new(src,destination/src.name)
            if common.sha(destination/src.name)!=common.sha(src):raise ValueError('Published bytes changed')
        result={'version':version,'git_head':head,'files':[common.describe(destination/p.name) for p in published],
                'selected_regression':selected,'core':core_receipt,'outer':fullcheck,'starter':startcheck,'fresh_core':recovered,
                'fragments_reassembled':joined,'publication_integrity_checked':True,'native_windows_acceptance':False,'client_download_confirmed':False}
        (destination/(prefix+'_VERIFIED.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        (destination/(prefix+'_SHA256.txt')).write_text('\n'.join(x['sha256']+'  '+x['name'] for x in result['files'])+'\n',encoding='utf-8')
        (destination/'PARTS.json').write_text(json.dumps(parts,indent=2),encoding='utf-8');return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2]);p.add_argument('--output',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--example',type=Path,required=True);a=p.parse_args();print(json.dumps(build(a.repo.resolve(),a.output.resolve(),a.evidence.resolve(),a.example.resolve()),ensure_ascii=False,indent=2))

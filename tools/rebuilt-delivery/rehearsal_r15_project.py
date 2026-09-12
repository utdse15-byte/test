"""Exercise existing film-project interchange using an isolated real-media copy.

The XML edit is performed by this fixture script, not a certified external NLE.
"""
from __future__ import annotations
from pathlib import Path
import argparse,json,os,shutil,subprocess,sys,re,zipfile
from manju.authoring.core import file_digest
from manju.core.container import Project
from manju.build.roundtrip import plan_roundtrip,apply_roundtrip


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();source=a.source.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    original={f.relative_to(source).as_posix():file_digest(f) for f in source.rglob('*') if f.is_file()}
    project=out/'project.manju';shutil.copytree(source,project)
    environment={**os.environ,'MANJU_RECENTS':str(out/'recents.json'),'MANJU_EXECUTION_MODE':'strict_zero_cost'}
    commands=[]
    def cli(label,*args):
        cmd=[sys.executable,'-m','manju',*args]
        r=subprocess.run(cmd,cwd=project,env=environment,capture_output=True,text=True,encoding='utf-8',timeout=15)
        (out/(label+'.stdout.txt')).write_text(r.stdout,encoding='utf-8');(out/(label+'.stderr.txt')).write_text(r.stderr,encoding='utf-8')
        (out/(label+'.rc')).write_text(str(r.returncode))
        commands.append({'command':cmd,'rc':r.returncode})
        if r.returncode:raise RuntimeError(f'{label}: {r.stdout}\n{r.stderr}')
        return json.loads(r.stdout)
    exported=cli('export','export','--srt','--ttml','--otio','--edl','--fcpxml','--xmeml','--pullsheet','--yes','--json')
    xml=next((project/'exports/fcpxml').glob('*.fcpxml'))
    pristine=plan_roundtrip(Project(project),xml)
    assert all(r.get('class')=='no_changes' for r in pristine['rows']),pristine
    # Trim 0.25s from the head of the first 2s synthetic take, keeping its end in bounds.
    text=xml.read_text(encoding='utf-8')
    text,n=re.subn(r'(<asset-clip\b[^>]*?name="S001"[^>]*?)start="[^"]*"',r'\1start="6/24s"',text,count=1);assert n==1
    text,n=re.subn(r'(<asset-clip\b[^>]*?name="S001"[^>]*?)duration="[^"]*"',r'\1duration="42/24s"',text,count=1);assert n==1
    xml.write_text(text,encoding='utf-8')
    roundtrip=plan_roundtrip(Project(project),xml)
    trims=[i for i,r in enumerate(roundtrip['rows']) if r.get('class')=='trim'];assert trims,roundtrip
    # Explicit accepted rows affect only this synthetic copy, never the original fixture.
    applied=apply_roundtrip(Project(project),roundtrip,rows=trims)
    (out/'roundtrip.json').write_text(json.dumps({'plan':roundtrip,'apply':applied},ensure_ascii=False,indent=2),encoding='utf-8')
    assert applied.get('applied'),applied
    archive=out/'synthetic.manjupkg';packed=cli('pack','pack','--full','--out',str(archive),'--json')
    restored=out/'restored.manju';unpacked=cli('unpack','unpack',str(archive),'--dest',str(restored),'--json')
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        copied=0
        for info in z.infolist():
            path=restored/info.filename
            if path.is_file():
                assert path.read_bytes()==z.read(info.filename),info.filename;copied+=1
    assert copied>20 and len(list(restored.rglob('*.mp4')))>=4
    assert original=={f.relative_to(source).as_posix():file_digest(f) for f in source.rglob('*') if f.is_file()}
    result={'ok':True,'export_outputs':exported,'fcpxml_baseline_no_spurious_changes':True,
       'script_edited_fcpxml_trim_applied_to_synthetic_copy':True,'native_nle_app_tested':False,
       'pack':packed,'unpack':unpacked,'restored_files_compared_with_zip':copied,
       'original_fixture_unchanged':True,'external_effects_styles_universal_import':False,
       'commercial_generation_calls':0,'commands':commands}
    (out/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

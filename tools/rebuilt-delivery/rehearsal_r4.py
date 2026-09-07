from pathlib import Path
from datetime import datetime,timezone
import subprocess,json,os,sys
from manju.authoring.core import *
root=Path(sys.argv[1]);root.mkdir(exist_ok=True)
for name,color in [('首帧.png','red'),('尾帧.png','blue')]:
 subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s=320x180','-frames:v','1','-threads','1','-y',str(root/name)],check=True,timeout=10)
assets=[Asset(id=f'A{i}',role=role,path=name,bytes=(root/name).stat().st_size,sha256=file_digest(root/name))
        for i,(role,name) in enumerate([('first_frame','首帧.png'),('last_frame','尾帧.png')],1)]
req=Request(shot_id='首尾桥接演练',task='bridge',prompt='在同一个连续镜头中，从首帧自然过渡到尾帧。',duration_s=8,resolution='768P',aspect_ratio='adaptive',assets=assets,preserve=['构图','主体身份'],change=['光线'])
cat=load_catalog();ap=approve(req,cat,'minimax-h3','first-last',reviewer='合成数据测试，不是用户审批',human_confirmed=True,acknowledge_warnings=True)
write_new_json(root/'request.json',req);write_new_json(root/'approval.json',ap)
path=write_bundle(req,cat,ap,root,root/'verified-handoff')
before={a.path:file_digest(root/a.path) for a in assets}
verified=verify_bundle(path)
# Use the actual top-level CLI, not only a directly imported Typer group.
cli=subprocess.run([sys.executable,'-m','manju','models','verify',str(path)],text=True,capture_output=True,timeout=30,check=True)
(root/'cli.log').write_text(cli.stdout+cli.stderr)
copy=root/'tampered-handoff';shutil.copytree(path,copy);(copy/'BRIEF.md').write_text('changed')
try:verify_bundle(copy)
except AuthoringError:tamper_rejected=True
else:raise AssertionError('tamper accepted')
assert before=={a.path:file_digest(root/a.path) for a in assets}
result={**verified,'actual_png_inputs':True,'source_bytes_unchanged':True,'tamper_rejected':tamper_rejected,'top_level_cli_exit':cli.returncode,'commercial_generation_calls':0}
write_new_json(root/'RESULT.json',result);print(json.dumps(result,ensure_ascii=False))

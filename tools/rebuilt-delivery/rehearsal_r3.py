from pathlib import Path
import hashlib,json,os,sys,threading,time
from manju.core.container import Project
from manju.core.models import ShotSpec
from manju.core.yamlio import write_yaml
from manju.build.graph import run_build
from manju.media.ffmpeg import run_ffmpeg,atomic_output,MediaCanceled
from manju.providers.caption_card import CaptionCardProvider
root=Path(sys.argv[1]); root.mkdir(parents=True,exist_ok=True)
os.environ['MANJU_EXECUTION_MODE']='strict_zero_cost'
os.environ['MANJU_RECENTS']=str(root/'recents.json')
p=Project.create(root/'恢复测试',git_init=False)
cfg=p.load_config().model_copy(update={'width':320,'height':180,'fps':24,'format':'16:9'})
p.save_config(cfg)
write_yaml(p.root/'bible/scenes.yaml',{'room':{'name':'测试房间'}})
write_yaml(p.root/'bible/characters.yaml',{})
for i in range(1,4):
 shot=ShotSpec.model_validate({'id':f'S00{i}','scene':'room','characters':[], 'duration':2,
     'action':{'main':f'本地恢复验证 {i}'},'generation':{'provider':'caption_card','params':{'renderer':'drawtext'}}})
 p.save_shot(shot)
idx=p.load_index();idx.order=['S001','S002','S003'];p.save_index(idx)
ev=threading.Event();original=CaptionCardProvider._register
# Simulated user action exactly after an actual clip has been registered.
def register(self,*args,**kwargs):
 t=original(self,*args,**kwargs);ev.set();return t
CaptionCardProvider._register=register
canceled=run_build(p,should_cancel=ev.is_set)
CaptionCardProvider._register=original
assert canceled.canceled and len(canceled.generated)==1,canceled.to_dict()
first={str(f.relative_to(p.root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.root.rglob('*.mp4')}
resumed=run_build(p)
assert len(p.takes('S001'))==len(p.takes('S002'))==len(p.takes('S003'))==1,resumed.to_dict()
assert all(p.load_shot(s).status.selected_take is None for s in idx.order)
# Explicit selection applies only to this synthetic test fixture.
for sid in idx.order:
 p.update_shot_raw(sid,lambda d,sid=sid:d.setdefault('status',{}).update(selected_take=p.takes(sid)[0].name))
complete=run_build(p,gen='off')
assert complete.ok,complete.to_dict()
final=p.root/complete.render_path
cached=run_build(p,gen='off');assert cached.ok
assert all(hashlib.sha256((p.root/k).read_bytes()).hexdigest()==v for k,v in first.items())
oldhash=hashlib.sha256(final.read_bytes()).hexdigest()
ev.clear();timer=threading.Timer(.3,ev.set);timer.start();start=time.monotonic()
try:
 with atomic_output(final) as staged:
  run_ffmpeg(['-re','-f','lavfi','-i','testsrc2=s=320x180:r=24:d=20',str(staged)],cancel_event=ev)
 raise AssertionError('cancel did not stop the encoder')
except MediaCanceled:
 elapsed=time.monotonic()-start
finally:
 timer.join(timeout=2)
assert hashlib.sha256(final.read_bytes()).hexdigest()==oldhash
run_ffmpeg(['-i',str(final),'-f','null','-'])
import subprocess
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(final)]))
report={'ok':True,'project':p.root.name,'canceled_generation':canceled.to_dict(),
 'resumed_build':resumed.to_dict(),'final_build':complete.to_dict(),'cache_build':cached.to_dict(),
 'cancel_elapsed_seconds':elapsed,'old_media_preserved':len(first),'final_unchanged_on_cancel':True,
 'final_sha256':oldhash,'final_duration_seconds':float(probe['format']['duration']),
 'fixture_selection_was_explicit':True,'commercial_generation_called':False}
(root/'RESULT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))

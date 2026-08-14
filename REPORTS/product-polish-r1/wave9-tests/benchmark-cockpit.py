from __future__ import annotations
import json, os, shutil, statistics, sys, time
from pathlib import Path
from manju.core.container import Project
from manju.core.models import ShotSpec
from manju.core.yamlio import write_yaml
from manju.gui.cockpit import cockpit_data

os.environ['MANJU_EXECUTION_MODE']='strict_zero_cost'
root=Path(sys.argv[1]).resolve()
results={}
for count in (12,100,300):
    pth=root/f'bench-{count}'
    if pth.exists(): shutil.rmtree(pth)
    p=Project.create(pth,name=f'bench-{count}',git_init=False)
    write_yaml(p.root/'bible'/'scenes.yaml',{'scene':{'name':'场景','description':'本地性能样本'}})
    write_yaml(p.root/'bible'/'characters.yaml',{'person':{'name':'人物','appearance':'深色外套'}})
    story={
      'brief.md':'# 故事与结尾\n\n一个人在雨夜完成必须完成的选择。'*10,
      'synopsis.md':'# 场次梗概\n\n开端、变化、选择、结尾。'*15,
      'beats.md':'# 变化节拍\n\n- 开始\n- 受阻\n- 选择\n- 结束\n'*10,
      'script.md':'# 剧本与声音\n\n夜。雨。人物停下，然后向前。'*20,
    }
    for n,t in story.items(): (p.root/'story'/n).write_text(t,encoding='utf-8')
    idx=p.load_index()
    for i in range(count):
      sid=f'S{i+1:04d}'
      shot=ShotSpec.model_validate({'id':sid,'scene':'scene','characters':['person'],'duration':'auto'})
      p.save_shot(shot); idx.order.append(sid)
    p.save_index(idx)
    cockpit_data(p)  # warm filesystem/page caches
    samples=[]
    for _ in range(5):
      t=time.perf_counter(); cockpit_data(p); samples.append((time.perf_counter()-t)*1000)
    results[str(count)]={
      'samples_ms':[round(x,2) for x in samples],
      'median_ms':round(statistics.median(samples),2),
      'max_ms':round(max(samples),2),
    }
print(json.dumps({'schema':'manju.product-polish-local-benchmark/v1','wave':9,'results':results,'note':'Local synthetic read-only measurement; not a release threshold.'},ensure_ascii=False,indent=2))

from __future__ import annotations
import json, os, re, shutil, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

repo = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
work = Path(sys.argv[3]).resolve()
for candidate in (work, work.with_name(work.name + '.manju')):
    if candidate.exists(): shutil.rmtree(candidate)
work.parent.mkdir(parents=True, exist_ok=True)

from manju.core.container import Project
from manju.build.director import propose
from manju.gui import create_page, director_page, glossary, page as app_page, pages, project_action
from manju.gui.userstate import set_mode, set_show_pro_terms

os.environ['MANJU_EXECUTION_MODE'] = 'strict_zero_cost'
os.environ['MANJU_GUI_STATE'] = str(work.parent / (work.name + '-gui-state.json'))
p = Project.create(work, name='polish-wave8', git_init=False)
set_mode('beginner')
set_show_pro_terms(False)

brief = '# 故事与结尾\n\n雨夜的末班车停在空站，准备离开的检票员发现一个孩子一直等着不会回来的父亲。她本来只想按时关门，却最终陪孩子走完整条回家路，也因此放下了自己多年没有面对的离别和逃避。\n'
synopsis = '# 场次梗概\n\n第一场从空站和催促关门开始，检票员发现孩子后被迫停下。第二场两人在雨里寻找线索，孩子承认父亲已经失约很多次。最后检票员把自己的伞交给孩子，并决定亲自送他回家，她也第一次拨通了多年未联系的母亲电话。\n'
beats = '# 变化节拍\n\n- 车站广播催促关门，检票员准备结束一天。\n- 她发现孩子仍然等在空站，选择先询问而不是赶走。\n- 孩子承认父亲可能不会来，检票员的态度发生变化。\n- 两人共撑一把伞离开，检票员也拨出自己的和解电话。\n'
script_full = '# 剧本与声音\n\n夜。空站。广播最后一次提示关门。检票员把钥匙插进卷帘门，忽然听见长椅下传来鞋底摩擦声。她回头，看见孩子抱着湿透的书包。孩子说：爸爸答应来。她停了很久，把门重新推开。广播声被她亲手关掉，空站第一次真正安静下来。\n'
script_partial = '# 剧本与声音\n\n夜。空站。检票员准备拉下卷帘门，忽然听见长椅下传来鞋底摩擦声。\n'
for rel, text in [('story/brief.md', brief),('story/synopsis.md',synopsis),('story/beats.md',beats),('story/script.md',script_partial)]:
    path=p.root/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text,encoding='utf-8')

css_common = (app_page.render_css() + '\n' + pages.render_pages_css() + '\n' + glossary.render_glossary_css() + '\n' + project_action.render_project_action_css())

def inline(html: str, css: str) -> str:
    html = re.sub(r'<link rel="stylesheet" href="[^"]+">\n?', '', html)
    html = re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>\n?', '', html)
    html = html.replace('</head>', '<style>'+css+'</style></head>')
    return html

def shot(name: str, html: str, css: str, viewport: dict[str,int]):
    full = inline(html, css)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox','--disable-gpu'])
        pg=browser.new_page(viewport=viewport, device_scale_factor=1)
        pg.set_content(full, wait_until='load')
        pg.evaluate("document.documentElement.classList.add('screenshots-ready')")
        png=out/(name+'.png')
        pg.screenshot(path=str(png), full_page=True)
        metrics=pg.evaluate('''() => ({
          viewport:{w:innerWidth,h:innerHeight},
          document:{w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight},
          main: (()=>{const e=document.querySelector('main');if(!e)return null;const r=e.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height}})(),
          journey: (()=>{const e=document.querySelector('[data-authoring-journey]');if(!e)return null;const r=e.getBoundingClientRect();return{x:r.x,y:r.y,w:r.width,h:r.height}})(),
          horizontalOverflow: document.documentElement.scrollWidth-innerWidth
        })''')
        (out/(name+'.json')).write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()

create_html=create_page.render_create(p,'tok')
create_css=css_common+create_page.render_create_css()
shot('authoring-create-desktop',create_html,create_css,{'width':1440,'height':900})
shot('authoring-create-narrow',create_html,create_css,{'width':390,'height':844})

(p.root/'story/script.md').write_text(script_full,encoding='utf-8')
propose(p,[{'type':'truth_patch_set','patches':[
 {'path':'story/script.md','content':script_full.replace('她停了很久，把门重新推开','她先关掉广播，随后重新抬起卷帘门')},
 {'path':'story/synopsis.md','content':synopsis.replace('决定亲自送他回家','决定关闭车站后亲自送他回家')},
]}],why='让人物的选择通过动作成立，并把结尾声音提前',actor='ai')
director_html=director_page.render_director(p,'tok')
director_css=css_common+director_page.render_director_css()
shot('authoring-director-desktop',director_html,director_css,{'width':1440,'height':900})
shot('authoring-director-narrow',director_html,director_css,{'width':390,'height':844})
print(out)

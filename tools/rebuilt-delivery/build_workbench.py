"""Build a self-contained offline HTML file from committed local sources."""
from pathlib import Path
import json
import sys
root=Path(__file__).resolve().parents[2]
release=sys.argv[1] if len(sys.argv)>1 else json.loads((root/'DELIVERY_VERSION.json').read_text(encoding='utf-8'))['version']
template=(root/'tools/workbench.template.html').read_text(encoding='utf-8')
catalog=(root/'src/manju/authoring/data/catalog.json').read_text(encoding='utf-8')
script=(root/'tools/workbench.js').read_text(encoding='utf-8')
quality=(root/'src/manju/authoring/data/quality.json').read_text(encoding='utf-8')
script=script.replace('// QUALITY_SCRIPT',(root/'tools/workbench.quality.js').read_text(encoding='utf-8'))
review_html=root/'tools/workbench.review.html'
review_js=root/'tools/workbench.review.js'
if review_html.is_file() and review_js.is_file():
 template=template.replace('<!-- REVIEW_SECTION -->',review_html.read_text(encoding='utf-8'))
 script=script.replace('// REVIEW_SCRIPT',review_js.read_text(encoding='utf-8'))
workspace_js=root/'tools/workbench.workspace.js'
if workspace_js.is_file():
 script=script.replace('// WORKSPACE_SCRIPT',workspace_js.read_text(encoding='utf-8'))
returns_js=root/'tools/workbench.returns.js'
if returns_js.is_file():
 script=script.replace('// RETURNS_SCRIPT',returns_js.read_text(encoding='utf-8'))
catalog_js=root/'tools/workbench.catalog.js'
if catalog_js.is_file():
 script=script.replace('// CATALOG_SCRIPT',catalog_js.read_text(encoding='utf-8'))
repair_html=root/'tools/workbench.repair.html'
repair_js=root/'tools/workbench.repair.js'
if repair_html.is_file() and repair_js.is_file():
 template=template.replace('<!-- REPAIR_SECTION -->',repair_html.read_text(encoding='utf-8'))
 script=script.replace('// REPAIR_SCRIPT',repair_js.read_text(encoding='utf-8'))
template=template.replace('<!-- DIRECTOR_SECTION -->',(root/'tools/workbench.director.html').read_text(encoding='utf-8'))
script=script.replace('// DIRECTOR_SCRIPT',(root/'tools/workbench.director.js').read_text(encoding='utf-8'))
template=template.replace('<!-- RETOUCH_SECTION -->',(root/'tools/workbench.retouch.html').read_text(encoding='utf-8'))
image_routes=(root/'src/manju/authoring/data/image_routes.json').read_text(encoding='utf-8')
script=script.replace('// RETOUCH_SCRIPT',(root/'tools/workbench.retouch.js').read_text(encoding='utf-8').replace('__IMAGE_ROUTES__',image_routes))
template=template.replace('<!-- STUDIO_SECTION -->',(root/'tools/workbench.studio.html').read_text(encoding='utf-8'))
script=script.replace('// STUDIO_SCRIPT',(root/'tools/workbench.studio.js').read_text(encoding='utf-8'))
template=template.replace('<!-- FLEX_SECTION -->',(root/'tools/workbench.flex.html').read_text(encoding='utf-8'))
script=script.replace('// FLEX_SCRIPT',(root/'tools/workbench.flex.js').read_text(encoding='utf-8'))
template=template.replace('<!-- EXCHANGE_SECTION -->',(root/'tools/workbench.exchange.html').read_text(encoding='utf-8'))
script=script.replace('// EXCHANGE_SCRIPT',(root/'tools/workbench.exchange.js').read_text(encoding='utf-8'))
template=template.replace('<!-- DESK_SECTION -->',(root/'tools/workbench.desk.html').read_text(encoding='utf-8'))
script=script.replace('// DESK_SCRIPT',(root/'tools/workbench.desk.js').read_text(encoding='utf-8'))
template=template.replace('<!-- RELINK_SECTION -->',(root/'tools/workbench.relink.html').read_text(encoding='utf-8'))
script=script.replace('// RELINK_SCRIPT',(root/'tools/workbench.relink.js').read_text(encoding='utf-8'))
template=template.replace('<!-- EXPERIENCE_SHELL -->',(root/'tools/workbench.experience.html').read_text(encoding='utf-8')+(root/'tools/workbench.inspection.html').read_text(encoding='utf-8'))
template=template.replace('/* EXPERIENCE_STYLE */',(root/'tools/workbench.experience.css').read_text(encoding='utf-8')+(root/'tools/workbench.inspection.css').read_text(encoding='utf-8'))
experience=(root/'tools/workbench.experience.js').read_text(encoding='utf-8')+'\n'+(root/'tools/workbench.inspection.js').read_text(encoding='utf-8')
template=template.replace('__EXPERIENCE_SCRIPT__',experience.replace('</script','<\\/script'))
# Do not allow JSON strings or program string literals to close a script tag.
html=template.replace('__CATALOG__',catalog.replace('</','<\\/')).replace('__SCRIPT__',script.replace('</script','<\\/script')).replace('__RELEASE__',release).replace('__QUALITY__',quality.replace('</','<\\/'))
for path in [root/'tools/model_workbench.html',root/'src/manju/authoring/data/workbench.html']:
 path.write_text(html,encoding='utf-8')
print(len(html.encode()),'bytes',release)

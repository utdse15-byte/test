"""Build a self-contained offline HTML file from committed local sources."""
from pathlib import Path
import json
import sys
root=Path(__file__).resolve().parents[2]
release=sys.argv[1] if len(sys.argv)>1 else 'R5'
template=(root/'tools/workbench.template.html').read_text(encoding='utf-8')
catalog=(root/'src/manju/authoring/data/catalog.json').read_text(encoding='utf-8')
script=(root/'tools/workbench.js').read_text(encoding='utf-8')
review_html=root/'tools/workbench.review.html'
review_js=root/'tools/workbench.review.js'
if review_html.is_file() and review_js.is_file():
 template=template.replace('<!-- REVIEW_SECTION -->',review_html.read_text(encoding='utf-8'))
 script=script.replace('// REVIEW_SCRIPT',review_js.read_text(encoding='utf-8'))
workspace_js=root/'tools/workbench.workspace.js'
if workspace_js.is_file():
 script=script.replace('// WORKSPACE_SCRIPT',workspace_js.read_text(encoding='utf-8'))
# Do not allow JSON strings or program string literals to close a script tag.
html=template.replace('__CATALOG__',catalog.replace('</','<\\/')).replace('__SCRIPT__',script.replace('</script','<\\/script')).replace('__RELEASE__',release)
for path in [root/'tools/model_workbench.html',root/'src/manju/authoring/data/workbench.html']:
 path.write_text(html,encoding='utf-8')
print(len(html.encode()),'bytes',release)

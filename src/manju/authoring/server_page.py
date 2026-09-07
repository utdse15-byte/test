"""Read-only GUI entry: download a shot request, then bind files locally.

The workbench has no fetch calls or write-back endpoint. Imported task snapshots
are explicit user file actions, never automatic replacements of browser drafts.
"""
from __future__ import annotations
from html import escape
from importlib.resources import files
from urllib.parse import quote
from ..core.container import Project

HEADERS = {
    'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src blob: data:; media-src blob: data:; connect-src 'none'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
}


def render(project: Project | None) -> str:
    html = files('manju.authoring').joinpath('data/workbench.html').read_text(encoding='utf-8')
    if project is None:
        content = '<p class="hint">尚未绑定影片工程。你仍可直接规划任务、审片和导出；也可以先在原工作台打开工程。</p>'
    else:
        shots = project.shot_ids()
        links = ''.join(
            '<a class="filebtn" href="/api/model-authoring/shot?shot=' + quote(shot, safe='') +
            '" download="MANJU_SHOT.json">下载镜头 ' + escape(shot) + ' 的任务</a>' for shot in shots)
        content = ('<p class="hint">先下载一个镜头任务，再用下方“导入任务 / 镜头”打开。导出是只读操作，'
                   '不会改写工程，也不会自动替换当前浏览器草稿。实际素材仍需重新绑定。</p>'
                   '<details><summary>当前工程的镜头（' + str(len(shots)) + '）</summary>'
                   '<div class="actions">' + (links or '<p>此工程还没有镜头。</p>') + '</div></details>')
    section = ('<section class="panel" id="project-model-tools"><h2>从原工作台进入，不必使用命令行</h2>'
               + content + '<p class="hint"><a href="/" target="_blank" rel="noopener noreferrer">打开影片工作台</a>'
               ' · 此页面不调用模型 API，不自动选片或锁片。</p></section>')
    return html.replace('<main>', '<main>\n' + section, 1)

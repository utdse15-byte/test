#!/usr/bin/env python3
"""Zero-cost R2 acceptance: real renderer/JS + real loopback service + media CLI.

The browser loads server-rendered documents with set_content and its fetch calls
are forwarded to the fixture's real HTTP service. This is deliberately NOT live
browser localhost E2E, and cannot certify CSP/navigation/Windows packaging.
Only disposable synthetic material is written; no providers are executed.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import wave

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


def fixture(work: Path):
    from manju.core.container import Project
    from manju.core.models import ShotSpec, TakeSidecar
    from manju.core.yamlio import write_yaml

    project = Project.create(work / "中文 空格 验收.manju", git_init=False)
    write_yaml(project.root / "bible/scenes.yaml", {
        "station": {"name": "雨夜站台", "description": "合成测试场景", "lighting": "冷白灯"}})
    for i in range(1, 4):
        sid = f"S{i:03d}"
        project.save_shot(ShotSpec(id=sid, scene="station", duration=2,
                                  action={"main": f"合成验收镜头 {i}，不是生成影片"}))
    index = project.load_index()
    index.order = ["S001", "S002", "S003"]
    project.save_index(index)
    clip = work / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=24", "-t", "2", "-c:v", "libx264",
                    "-threads", "1", "-pix_fmt", "yuv420p", "-an", str(clip)],
                   check=True, capture_output=True, timeout=45)
    for sid in index.order[:2]:
        take = project.register_take(sid, clip, TakeSidecar(provider="manual_import", spec_hash="manual"))
        if sid == "S001":
            shot = project.load_shot(sid)
            shot.status.selected_take = take.name
            project.save_shot(shot)
    with wave.open(str(project.imports_dir / "合成提示音.wav"), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * 8000)
    # Small valid keyframes, decoded from the synthetic source (no image model).
    for name, second in [("首帧.png", "0"), ("尾帧.png", "1.5")]:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", second, "-i", str(clip),
                        "-frames:v", "1", "-threads", "1", str(project.imports_dir / name)],
                       check=True, capture_output=True, timeout=45)
    shot = project.load_shot("S003")
    raw = shot.model_dump()
    raw["keyframes"] = [{"position": "start", "image": "media/imports/首帧.png"},
                        {"position": "end", "image": "media/imports/尾帧.png"}]
    project.save_shot(ShotSpec.model_validate(raw))
    return project, clip


class Service:
    def __init__(self, project):
        from manju.gui.server import create_server
        self.server = create_server(project, host="127.0.0.1", port=0, actor="human", app_mode=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = self.server.url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.calls: list[dict] = []

    def request(self, path, method="GET", headers=None, body=None):
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise ValueError("Only fixture-service relative URLs are allowed")
        request = urllib.request.Request(self.base + path, data=body,
                                         headers=headers or {}, method=method)
        try:
            response = self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status, headers, data = response.status, dict(response.headers), response.read()
        # Never persist the server token, arbitrary headers, or user text.
        self.calls.append({"method": method, "path": path, "status": status})
        return status, headers, data

    async def bridge(self, payload):
        body = base64.b64decode(payload["body_b64"]) if payload.get("body_b64") else None
        path = payload["url"]
        # Real watch endpoint, with an explicitly recorded shorter long-poll
        # timeout. This keeps teardown bounded; event data is never fabricated.
        parsed = urlsplit(path)
        if parsed.path == "/api/watch":
            query = dict(parse_qsl(parsed.query))
            query["timeout"] = "1"
            path = urlunsplit(("", "", parsed.path, urlencode(query), ""))
        code, headers, data = await asyncio.to_thread(
            self.request, path, payload.get("method", "GET"), payload.get("headers"), body)
        return {"status": code, "headers": headers, "body_b64": base64.b64encode(data).decode()}

    def mount(self, browser, path, *, width=1440):
        code, _, data = self.request(path)
        assert code == 200, (path, code)
        html = data.decode("utf-8")
        scripts = re.findall(r'<script[^>]+src="([^"]+)"[^>]*></script>', html)
        styles = re.findall(r'<link[^>]+href="([^"]+\.css)"[^>]*>', html)
        html = re.sub(r'<script[^>]+src="[^"]+"[^>]*></script>', '', html)
        html = re.sub(r'<link[^>]+href="[^"]+"[^>]*>', '', html)
        for src in dict.fromkeys(re.findall(r'\bsrc="([^"]+)"', html)):
            if src.startswith('/'):
                status, hs, binary = self.request(src.replace('&amp;', '&'))
                if status == 200:
                    embedded = 'data:' + hs.get('Content-Type', 'application/octet-stream') + ';base64,'
                    html = html.replace('src="' + src + '"', 'src="' + embedded +
                                        base64.b64encode(binary).decode() + '"')
        css = '\n'.join(self.request(path)[2].decode() for path in styles)
        html = html.replace('</head>', '<style>' + css + '</style></head>')
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # No actual browser network is needed by this composed transport.
        page.route("**/*", lambda route: route.abort())
        page.expose_function("__fixture_http", self.bridge)
        page.evaluate("""() => {window.__fixturePending=0; window.__fixtureClosing=false;
          window.fetch=async function(input,opts={}) {
            if(window.__fixtureClosing) throw new Error('Fixture page is closing');
            window.__fixturePending++;
            try {
            let headers={};new Headers(opts.headers||{}).forEach((v,k)=>headers[k]=v);
            let body=opts.body;let bytes=body instanceof Blob?new Uint8Array(await body.arrayBuffer()):new TextEncoder().encode(body||'');
            let chars='';for(let i=0;i<bytes.length;i++)chars+=String.fromCharCode(bytes[i]);
            const r=await window.__fixture_http({url:String(input),method:opts.method||'GET',headers,body_b64:body?btoa(chars):null});
            return new Response(Uint8Array.from(atob(r.body_b64),c=>c.charCodeAt(0)),{status:r.status,headers:r.headers});
            } finally {window.__fixturePending--;}
        };}""")
        page.set_content(html, wait_until="domcontentloaded")
        for src in scripts:
            page.add_script_tag(content=self.request(src)[2].decode())
        page.wait_for_timeout(120)
        return page, errors

    @staticmethod
    def dispose_page(page):
        # Drain real HTTP callbacks before destroying their JS binding target.
        page.evaluate("window.__fixtureClosing=true")
        page.wait_for_function("window.__fixturePending === 0", timeout=35000)
        page.unroute_all(behavior="wait")
        page.close()

    def close(self):
        self.server.shutdown()
        self.server.close()
        self.thread.join(timeout=5)


def wait_write(page, service, path, click, expected=200):
    before = len(service.calls)
    click()
    for _ in range(100):
        page.wait_for_timeout(50)
        hits = [row for row in service.calls[before:] if row['method'] == 'POST' and row['path'] == path]
        if hits:
            assert hits[-1]['status'] == expected, hits[-1]
            return
    raise AssertionError(f"No completed POST {path}")


def browser_checks(project, service, executable, out):
    from playwright.sync_api import sync_playwright
    facts = {"surfaces": [], "actions": []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=executable, headless=True,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            for width in (1440, 390):
                for route in ('/', '/create', '/storyboard', '/lab?shot=S001', '/review?shot=S001',
                              '/edit', '/subtitles', '/mixer', '/packaging', '/exports', '/director', '/ingest'):
                    page, errors = service.mount(browser, route, width=width)
                    if route == '/':
                        # Home is genuinely asynchronous. Wait for its real
                        # service-backed hero, not an arbitrary screenshot
                        # delay that can capture a passing loading skeleton.
                        page.wait_for_selector('#cockpit .ck-hero', timeout=30000)
                    overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
                    assert not errors, (route, errors)
                    assert not overflow, (route, width, "horizontal overflow")
                    name = route.strip('/').replace('?', '_').replace('=', '_') or 'home'
                    page.screenshot(path=str(out / f'{width}-{name}.png'), full_page=True)
                    facts['surfaces'].append({"route": route, "width": width, "status": 200,
                                              "horizontal_overflow": overflow, "page_errors": errors,
                                              "home_ready": True if route == "/" else None})
                    service.dispose_page(page)
                    (out / 'surface-results.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')
            page, errors = service.mount(browser, '/mixer')
            assert page.locator('#sfx-body .sfx-row').count() == 0
            page.locator('#sfx-add').click()
            page.locator('.sfx-source').select_option('media/imports/合成提示音.wav')
            page.locator('.sfx-at').fill('S001')
            wait_write(page, service, '/api/mixer/apply', lambda: page.locator('#mx-apply').click())
            assert len(project.load_rules().audio.sfx) == 1
            page.locator('[data-act=sfx-del]').click()
            wait_write(page, service, '/api/mixer/apply', lambda: page.locator('#mx-apply').click())
            assert not project.load_rules().audio.sfx
            page.locator('#sfx-add').click()
            assert page.locator('.sfx-gain').input_value() == '-6'
            assert not errors, errors
            facts['actions'].append('first SFX -> actual HTTP save -> disk check -> delete/save -> re-add')
            service.dispose_page(page)
            page, errors = service.mount(browser, '/packaging')
            assert page.locator('#ic-body .ic-row').count() == 0
            page.locator('#ic-add').click()
            page.locator('.ic-text').fill('第一章 · 合成验收')
            page.locator('.ic-at').fill('S001')
            save = lambda: page.locator('[data-act=save][data-section=info]').click()
            wait_write(page, service, '/api/packaging/apply', save)
            assert project.load_packaging().info_cards[0].text == '第一章 · 合成验收'
            page.locator('[data-act=ic-del]').click()
            wait_write(page, service, '/api/packaging/apply', save)
            assert not project.load_packaging().info_cards
            page.locator('#ic-add').click()
            assert page.locator('.ic-duration').input_value() == '1500'
            assert not errors, errors
            facts['actions'].append('first info card -> actual HTTP save -> disk check -> delete/save -> re-add')
            service.dispose_page(page)
            first, e1 = service.mount(browser, '/create')
            second, e2 = service.mount(browser, '/create')
            a = first.locator('.cw-text[data-stage=brief]')
            b = second.locator('.cw-text[data-stage=brief]')
            a.fill('窗口甲已保存的故事。')
            wait_write(first, service, '/api/create/save', lambda: first.locator('[data-act=save][data-stage=brief]').click())
            # Keep a real later draft so the normal delayed refresh is suppressed.
            a.fill('窗口甲仍在写，不能被刷新清空。')
            b.fill('窗口乙的旧快照草稿，冲突后也不能丢失。')
            wait_write(second, service, '/api/create/save', lambda: second.locator('[data-act=save][data-stage=brief]').click(), expected=409)
            second.wait_for_timeout(700)
            assert (project.root / 'story/brief.md').read_text(encoding='utf-8') == '窗口甲已保存的故事。\n'
            assert b.input_value() == '窗口乙的旧快照草稿，冲突后也不能丢失。'
            assert a.input_value() == '窗口甲仍在写，不能被刷新清空。'
            assert not e1 + e2, e1 + e2
            second.screenshot(path=str(out / 'story-conflict-preserved.png'), full_page=True)
            facts['actions'].append('two actual GUI buffers -> first HTTP save -> stale HTTP 409 -> both drafts preserved')
            service.dispose_page(first)
            service.dispose_page(second)
            page, errors = service.mount(browser, '/')
            page.keyboard.press('F1')
            page.wait_for_selector('#mj-help-center[open]')
            page.wait_for_function("window.ManjuHelpCenter.current().data !== null")
            assert page.evaluate("window.ManjuHelpCenter.current().data.execution.status") == 'strict'
            assert '本地安全' in page.locator('#mj-help-center').inner_text()
            page.keyboard.press('Escape')
            page.keyboard.press('Control+k')
            page.wait_for_selector('#mj-command-palette[open]')
            page.locator('#mj-command-input').fill('S001')
            page.wait_for_timeout(250)
            assert not errors, errors
            facts['actions'].append('F1 help reads actual about API; Ctrl+K opens and searches shot')
            service.dispose_page(page)
        finally:
            browser.close()
    return facts


def media_cli_checks(project, source, out):
    env = dict(os.environ, PYTHONPATH=str(REPO / 'src'))
    commands = []
    def cli(*args):
        command = [sys.executable, '-m', 'manju', *map(str, args)]
        run = subprocess.run(command, cwd=project.root, env=env, text=True,
                             capture_output=True, timeout=90)
        row = {'arguments': list(map(str, args)), 'exit_code': run.returncode,
               'stdout': run.stdout, 'stderr': run.stderr}
        commands.append(row)
        (out / 'cli-commands.json').write_text(json.dumps(commands, ensure_ascii=False, indent=2), encoding='utf-8')
        assert run.returncode == 0, row
        return json.loads(run.stdout)
    result = cli('handoff', 'create', 'S003', '--profile', 'portable_video', '--json')
    bundle = Path(result['bundle'])
    assert cli('handoff', 'verify', bundle, '--json')['ok'] is True
    assert (bundle / 'ASSET_MAP.md').exists()
    assert all(path.suffix == '.png' for path in (bundle / 'assets').iterdir())
    returned = source.with_name('S003_return.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-vf', 'hue=h=90',
                    '-c:v', 'libx264', '-threads', '1', '-an', str(returned)],
                   check=True, capture_output=True, timeout=45)
    assert not project.load_shot('S003').status.selected_take
    before = {p.relative_to(project.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for sid in project.shot_ids() for take in project.takes(sid)
              for p in (take.media_path, take.sidecar_path) if p is not None and p.is_file()}
    cli('ingest', returned, '--shot', 'S003', '--handoff', bundle,
        '--apply', '--no-auto-select', '--json')
    assert not project.load_shot('S003').status.selected_take
    takes = project.takes('S003')
    assert len(takes) == 1 and takes[0].media_path and takes[0].error is None
    sidecar = takes[0].sidecar.model_dump(mode='json')
    assert result['handoff_id'] in json.dumps(sidecar)
    assert sidecar['provider'] == 'manual_import'
    assert all(hashlib.sha256((project.root / p).read_bytes()).hexdigest() == digest
               for p, digest in before.items()), 'existing take bytes changed'
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                            '-of', 'json', str(takes[0].media_path)], text=True,
                           capture_output=True, check=True, timeout=30)
    media = json.loads(probe.stdout)
    assert any(stream['codec_type'] == 'video' for stream in media['streams'])
    assert float(media['format']['duration']) >= 1.9
    shutil.copytree(bundle, out / 'synthetic-handoff')
    (out / 'synthetic-take-sidecar.json').write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'verified_bundle': result['handoff_id'], 'new_takes': len(takes), 'auto_selected': False,
            'original_take_files_unchanged': len(before), 'duration_seconds': media['format']['duration'],
            'fixture_only': True, 'real_generated_media': False, 'human_quality_approval': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--chromium', default=os.environ.get('MANJU_DEV_CHROMIUM') or shutil.which('chromium') or shutil.which('msedge'))
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    facts = {'started_at': datetime.now(timezone.utc).isoformat(),
             'runtime_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
             'mode': 'real renderer + actual JS + real HTTP service composition',
             'live_browser_http_e2e': False, 'watch_timeout_seconds': 1,
             'zero_cost': True, 'provider_generation': False}
    service = None
    try:
        if not args.chromium:
            raise RuntimeError('Local Chromium/Edge unavailable; nothing will be downloaded')
        with tempfile.TemporaryDirectory(prefix='manju-r2-dogfood-') as temp:
            work = Path(temp)
            for key, name in [('MANJU_GUI_STATE', 'gui.json'), ('MANJU_RECENTS', 'recents.json'),
                              ('MANJU_LIBRARY', 'library'), ('MANJU_PROVIDERS_DIR', 'providers')]:
                os.environ[key] = str(work / name)
            os.environ['MANJU_EXECUTION_MODE'] = 'strict_zero_cost'
            os.environ['NO_PROXY'] = os.environ['no_proxy'] = '*'
            project, source = fixture(work)
            service = Service(project)
            facts['browser'] = browser_checks(project, service, args.chromium, out)
            facts['http_calls'] = service.calls
            service.close(); service = None
            facts['media_roundtrip'] = media_cli_checks(project, source, out)
            facts['ok'] = True
    except Exception:
        facts['ok'] = False
        facts['exception'] = traceback.format_exc()
        print(facts['exception'], file=sys.stderr)
    finally:
        if service:
            facts['http_calls'] = service.calls
            service.close()
        facts['finished_at'] = datetime.now(timezone.utc).isoformat()
        (out / 'acceptance.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(facts, ensure_ascii=False, indent=2))
    return 0 if facts.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())

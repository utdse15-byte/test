"""Open a real R14 composition with the untouched, separately unpacked R13.

Inputs: a verified R13 package directory, a new Studio ZIP, and an empty output
location. This uses R13's Python reader and exact R13 HTML, not a rewritten shim.
Browser transport is isolated_document, not native Windows file navigation.
"""
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('r13', type=Path)
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    base, archive, out = args.r13.resolve(), args.archive.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    identity = json.loads((base / 'PACKAGE.json').read_text(encoding='utf-8'))
    assert identity['stage'] == 'R13'
    src = base / 'source/src'
    command = [sys.executable, '-c',
               'import json,sys,manju; from pathlib import Path; '
               'from manju.authoring.studio import verify_studio; '
               'print(json.dumps({"import":manju.__file__,"version":manju.__version__, '
               '"verification":verify_studio(Path(sys.argv[1]))}))', str(archive)]
    result = subprocess.run(command, cwd=out, env={**os.environ, 'PYTHONPATH': str(src)},
                            check=True, capture_output=True, text=True, encoding='utf-8', timeout=60)
    python = json.loads(result.stdout)
    assert Path(python['import']).is_relative_to(src)
    assert python['version'] == '0.2.0+r13' and python['verification']['ok']
    html = base / 'OPEN_MODEL_WORKBENCH.html'
    hashes = json.loads((base / 'SHA256SUMS.json').read_text(encoding='utf-8'))
    assert sha256(html.read_bytes()).hexdigest() == hashes['OPEN_MODEL_WORKBENCH.html']
    assert html.read_bytes() == (src / 'manju/authoring/data/workbench.html').read_bytes()
    original = sha256(archive.read_bytes()).hexdigest()
    requests, errors = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=shutil.which('chromium'), headless=True,
                                     args=['--no-sandbox'])
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.on('request', lambda req: requests.append(req.url))
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.set_content(html.read_text(encoding='utf-8'), wait_until='load')
        assert page.evaluate('typeof window.ManjuFlex === "undefined"')
        page.locator('#import-studio').set_input_files(archive)
        expect(page.locator('#studio-restore-preview')).to_be_visible()
        page.locator('#studio-restore-confirmed').check()
        page.locator('#apply-studio').click()
        expect(page.locator('#studio-save-status')).to_contain_text('三个工作区已从实际核验')
        for selector in ['#director-video', '#repair-video']:
            page.locator(selector).evaluate('v=>v.play()')
            page.wait_for_function('s=>document.querySelector(s).currentTime>0.2', arg=selector)
            page.locator(selector).evaluate('v=>v.pause()')
        assert page.locator('#quality-only').is_checked()
        with page.expect_download() as item:
            page.locator('#export-studio').click()
        saved = out / 'R13_REEXPORTED.zip'
        item.value.save_as(saved)
        assert saved.read_bytes() == archive.read_bytes()
        assert not errors and not any(u.startswith(('http:', 'https:')) for u in requests)
        version = browser.version
        context.close()
        browser.close()
    assert sha256(archive.read_bytes()).hexdigest() == original
    report = {'ok': True, 'old_package_head': identity['git_head'], 'python': python,
              'old_html_sha256': sha256(html.read_bytes()).hexdigest(),
              'real_r13_browser_restored_r14_output': True,
              'real_videos_played_without_rebinding': True,
              'r13_reexport_byte_identical': True, 'archive_sha256': original,
              'original_archive_unchanged': True, 'browser_version': version,
              'http_requests': 0, 'page_errors': errors, 'transport': 'isolated_document',
              'native_windows_certified': False}
    (out / 'RESULT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

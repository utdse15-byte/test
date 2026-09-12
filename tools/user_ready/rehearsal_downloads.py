"""Recheck final real downloads, not a small synthetic replacement for them.

The original helper and start-page bytes run in separate isolated Chromium
pages. This is not native Windows navigation or proof the user's disk received
anything. The resulting browser download is checked again from disk.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from playwright.sync_api import sync_playwright, expect


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    full=a.release/'MANJU_RECOVER_FULL.zip';start=a.release/'MANJU_RECOVER_START.zip'
    helper=a.release/'MANJU_RECOVER_DOWNLOAD_HELPER.html'
    parts=sorted(a.release.glob('MANJU_RECOVER_PART_*.zip'));assert len(parts)==5
    renamed=a.output/'我的上手包.zip';shutil.copyfile(start,renamed)
    truncated=a.output/'incomplete.zip';truncated.write_bytes(start.read_bytes()[:-17])
    errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        context=browser.new_context(accept_downloads=True,viewport={'width':390,'height':850})
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.set_default_timeout(60000)
        page.set_content(helper.read_text(encoding='utf-8'),wait_until='load')
        for path in (full,renamed):
            page.locator('#check-file').set_input_files(path);expect(page.locator('#check-status')).to_contain_text('核验通过')
        page.locator('#check-file').set_input_files(truncated);expect(page.locator('#check-status')).to_contain_text('未通过')
        page.locator('#parts-files').set_input_files(parts[:-1]);expect(page.locator('#join')).to_be_disabled()
        page.locator('#parts-files').set_input_files([parts[0],parts[0],*parts[2:]])
        page.locator('#join').click();expect(page.locator('#parts-status')).to_contain_text('未完成');expect(page.locator('#save')).not_to_be_visible()
        page.locator('#parts-files').set_input_files(parts[::-1]);page.locator('#join').click()
        expect(page.locator('#save')).to_be_visible(timeout=120000)
        with page.expect_download(timeout=60000) as event:page.locator('#save').click()
        saved=a.output/'ACTUALLY_SAVED_FULL.zip';event.value.save_as(saved)
        assert sha(saved)==sha(full) and saved.stat().st_size==full.stat().st_size
        with zipfile.ZipFile(saved) as archive:assert archive.testzip() is None
        page.locator('#check-file').set_input_files(saved);expect(page.locator('#check-status')).to_contain_text('核验通过')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.screenshot(path=str(a.output/'download-helper-narrow.png'))
        # Independent pages avoid collisions between their top-level script names.
        with zipfile.ZipFile(start) as z:
            name=next(n for n in z.namelist() if n.endswith('/START_HERE.html'));entry_html=z.read(name).decode('utf-8')
        entry=context.new_page();entry.on('pageerror',lambda e:errors.append(str(e)));entry.set_content(entry_html,wait_until='load')
        with entry.expect_download(timeout=15000) as event:entry.locator('#smoke-download').click()
        sample=a.output/'MANJU_SAVE_TEST.txt';event.value.save_as(sample)
        entry.locator('#smoke-file').set_input_files(sample);expect(entry.locator('#smoke-status')).to_contain_text('保存演练通过')
        assert entry.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        entry.screenshot(path=str(a.output/'start-narrow.png'))
        context.close();browser.close()
    assert not errors,errors
    result={'ok':True,'full_bytes':full.stat().st_size,'full_sha256':sha(full),
            'full_and_renamed_start_checked':True,'truncated_rejected':True,'missing_part_rejected':True,'duplicate_part_rejected':True,
            'five_reversed_parts_reassembled_actually_downloaded_and_rechecked':True,
            'entry_save_exercise_actually_downloaded_and_rechecked':True,
            'narrow_width':390,'horizontal_overflow':False,'javascript_errors':errors,
            'native_navigation_verified':False,'windows_verified':False,'user_computer_download_confirmed':False}
    (a.output/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

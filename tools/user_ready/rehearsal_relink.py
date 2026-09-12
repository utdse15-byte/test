"""Actual-media reconnect and download rehearsal. Binding loss is fault injection.

Executes unchanged HTML in an isolated browser document, not native Windows/file navigation.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

import manju
from manju.authoring.desk import verify_desk
from playwright.sync_api import sync_playwright, expect


def sha(data):return hashlib.sha256(data).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--html',type=Path,required=True);p.add_argument('--example',type=Path,required=True)
    p.add_argument('--legacy-html',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();root=args.output;root.mkdir(parents=True,exist_ok=False)
    with zipfile.ZipFile(args.example) as outer,zipfile.ZipFile(io.BytesIO(outer.read('STUDIO.zip'))) as studio:
        doc=json.loads(studio.read('STUDIO.json'))
        originals={m['sha256']:studio.read('media/'+m['sha256']) for m in doc['media']}
    paths=[]
    for n,(h,data) in enumerate(originals.items()):
        path=root/f'搬家后重命名_{n:02d}.bin';path.write_bytes(data);paths.append(path)
    bad=root/'运动原片_合成.mp4';b=bytearray(next(iter(originals.values())));b[-1]^=1;bad.write_bytes(b);paths.append(bad)
    errors=[];contexts=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        def fresh(html):
            ctx=browser.new_context(accept_downloads=True,viewport={'width':1200,'height':900});contexts.append(ctx)
            page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.set_content(html.read_text(encoding='utf-8'),wait_until='load');return page
        def restore(page,path):
            page.locator('#intake-files').set_input_files(path);expect(page.locator('#intake-preview')).to_be_visible()
            page.locator('#intake-open').click();expect(page.locator('#desk-restore-preview')).to_be_visible(timeout=30000)
            page.locator('#desk-restore-confirmed').check();page.locator('#desk-restore-apply').click()
        page=fresh(args.html);restore(page,args.example)
        before=page.evaluate('ManjuDesk.fingerprint()')
        # Controlled fault injection: retain all logical records but remove File bindings.
        page.evaluate('''()=>{ManjuWorkbench.state.files.clear();ManjuReview.state.files.clear();ManjuRepair.state.entry=null;ManjuDirector.state.entry=null;ManjuDirector.state.files.clear();}''')
        page.locator('#relink-panel').evaluate('(e)=>e.open=true');page.locator('#relink-check').click()
        assert len(page.evaluate('ManjuRelink.inventory().filter(x=>x.missing)'))==5
        page.locator('#relink-files').set_input_files(paths)
        page.wait_for_function('()=>!!ManjuRelink.state.pending&&!ManjuRelink.state.busy',timeout=30000)
        assert page.evaluate('ManjuRelink.state.pending.matched.size')==5
        assert page.evaluate('ManjuRelink.state.pending.ignored.length')>=1
        assert page.evaluate('ManjuWorkbench.state.files.size')==0
        assert page.evaluate('ManjuDesk.fingerprint()')==before
        page.set_viewport_size({'width':390,'height':844});page.locator('#relink-panel').scroll_into_view_if_needed()
        page.screenshot(path=str(root/'relink-preview-narrow.png'));assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.locator('#relink-confirmed').check();page.locator('#relink-apply').click()
        expect(page.locator('#relink-status')).to_contain_text('已接回',timeout=30000)
        assert page.evaluate('ManjuDesk.fingerprint()')==before
        assert not page.evaluate('ManjuRelink.inventory().some(x=>x.missing)')
        assert page.evaluate('ManjuDesk.needsSave()')
        assert page.locator('#quality-only').is_checked()
        for id in ['human-confirmed','review-confirmed','promotion-confirmed']:assert not page.locator('#'+id).is_checked()
        with page.expect_download() as dl:page.locator('#desk-save').click()
        saved=root/'RECONNECTED_DESK.zip';dl.value.save_as(saved);verified=verify_desk(saved)
        with zipfile.ZipFile(saved) as outer,zipfile.ZipFile(io.BytesIO(outer.read('STUDIO.zip'))) as studio:
            for h,data in originals.items():assert studio.read('media/'+h)==data
        page.locator('#desk-verify-file').set_input_files(saved)
        expect(page.locator('#desk-save-status')).to_contain_text('已核验你选回的收工包')
        restored=fresh(args.html);restore(restored,saved)
        assert restored.evaluate('ManjuDesk.fingerprint()')==before
        played=restored.evaluate('''async()=>{let n=0;for(const v of document.querySelectorAll('video'))if(v.src.startsWith('blob:')){v.muted=true;await v.play();await new Promise((resolve,reject)=>{const start=performance.now();const check=()=>{if(v.currentTime>0.05){resolve();return;}if(performance.now()-start>8000){reject(Error('video did not advance'));return;}setTimeout(check,50);};check();});v.pause();n++;}return n;}''')
        assert played>=3
        legacy=fresh(args.legacy_html);restore(legacy,saved)
        assert legacy.evaluate('ManjuDesk.fingerprint()')==before
        with legacy.expect_download() as dl:legacy.locator('#desk-save').click()
        prior=root/'LEGACY_REEXPORTED.zip';dl.value.save_as(prior);assert prior.read_bytes()==saved.read_bytes()
        decoded=0;pixels=[]
        for m in doc['media']:
            data=originals[m['sha256']]
            if m['mime_type'].startswith('video/'):
                file=root/(m['sha256'][:12]+'.mp4');file.write_bytes(data)
                subprocess.run(['ffmpeg','-v','error','-i',str(file),'-f','null','-'],check=True,capture_output=True,timeout=30);decoded+=1
            elif data.startswith(b'\x89PNG'):
                import struct
                pixels.append(list(struct.unpack('>II',data[16:24])))
        assert [1920,1080] in pixels and not errors
        result=dict(ok=True,version=manju.__version__,runtime=manju.__file__,media_preserved=len(originals),videos_played=played,
            videos_fully_decoded=decoded,png_pixels=pixels,wrong_same_name_file_ignored=True,task_and_history_unchanged=True,
            browser_download_picked_back=True,legacy_reexport_identical=True,source_bytes_hash=sha(saved.read_bytes()),
            native_navigation_verified=False,windows_verified=False,fault_injection='logical records retained; only File bindings removed',javascript_errors=errors)
        (root/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
        browser.close()

if __name__=='__main__':main()

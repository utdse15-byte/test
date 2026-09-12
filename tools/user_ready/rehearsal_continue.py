"""Real ZIP/media continuation rehearsal in an isolated Chromium document.

The sandbox blocks native navigation. A test-only fetch adapter forwards to the
real HTTP server; this is not native end-to-end/Windows acceptance. Production
client code and server origin checks are not relaxed.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import threading
from urllib.parse import urlsplit
import zipfile

import manju
from manju.authoring.desk import verify_desk
from playwright.sync_api import sync_playwright, expect

BRIDGE = '''<script>window.fetch=async function(url,options={}){
 const body=options.body?new Uint8Array(await new Blob([options.body]).arrayBuffer()):null;
 let encoded=null;if(body){let chunks=[];for(let i=0;i<body.length;i+=16384)chunks.push(String.fromCharCode(...body.subarray(i,i+16384)));encoded=btoa(chunks.join(''));}
 const answer=await window.__local_http_test({path:String(url),method:options.method||'GET',headers:options.headers||{},body:encoded});
 return new Response(Uint8Array.from(atob(answer.body),c=>c.charCodeAt(0)),{status:answer.status,headers:answer.headers});
};</script>'''


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kit',type=Path,required=True);p.add_argument('--example',type=Path,required=True)
    p.add_argument('--legacy-html',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();root=args.output.absolute();root.mkdir(parents=True,exist_ok=False)
    spec=importlib.util.spec_from_file_location('tested_local_continue',args.kit/'LOCAL_CONTINUE.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    contexts=[];errors=[];servers=[];current={}
    def start():
        server,url=module.create_server(args.kit,root/'recovery-data')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        current.update(server=server,url=urlsplit(url),thread=thread);servers.append((server,thread))
    def close():
        current['server'].shutdown();current['server'].server_close();current['thread'].join(3)
        assert not current['thread'].is_alive()
    def call(request):
        url=current['url'];assert request['path'].startswith(url.path)
        conn=http.client.HTTPConnection('127.0.0.1',url.port,timeout=30)
        conn.request(request['method'],request['path'],body=base64.b64decode(request['body']) if request['body'] is not None else None,
                     headers={**request['headers'],'Origin':'http://'+url.netloc})
        response=conn.getresponse();value={'status':response.status,'headers':dict(response.getheaders()),'body':base64.b64encode(response.read()).decode()};conn.close();return value
    before=sha(args.example);start()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            def fresh(legacy=False):
                context=browser.new_context(accept_downloads=True,viewport={'width':1260,'height':940});contexts.append(context)
                page=context.new_page();page.set_default_timeout(20000);page.on('pageerror',lambda error:errors.append(str(error)))
                if legacy:html=args.legacy_html.read_text(encoding='utf-8')
                else:
                    page.expose_function('__local_http_test',call)
                    conn=http.client.HTTPConnection('127.0.0.1',current['url'].port,timeout=10);conn.request('GET',current['url'].path)
                    response=conn.getresponse();assert response.status==200;html=response.read().decode();conn.close();html=html.replace('<head>','<head>'+BRIDGE,1)
                page.set_content(html,wait_until='load');return page
            def import_desk(page,path):
                page.locator('#intake-files').set_input_files(path);expect(page.locator('#intake-preview')).to_be_visible()
                page.locator('#intake-open').click();expect(page.locator('#desk-restore-preview')).to_be_visible(timeout=40000)
                page.locator('#desk-restore-confirmed').check();page.locator('#desk-restore-apply').click()
            page=fresh();assert not (root/'recovery-data').exists()
            import_desk(page,args.example)
            page.locator('#prompt').fill('  本机续作演练的新稿。原媒体不变，未接受的外部意见留待决定。\n下一次继续。  ')
            expected=page.evaluate('ManjuDesk.fingerprint()');buffers=page.evaluate('ManjuDesk.view()')
            assert page.evaluate('ManjuDesk.needsSave()')
            page.locator('#continue-save').click();expect(page.locator('#continue-status')).to_contain_text('已写入并核对本机恢复点',timeout=60000)
            point=page.evaluate('ManjuContinuation.state.lastPoint');assert page.evaluate('ManjuDesk.needsSave()')
            local_file=root/'recovery-data'/point['id']/'DESK.zip';receipt=verify_desk(local_file)
            assert receipt['studio']['media_files']==5
            page.set_viewport_size({'width':390,'height':844});page.locator('#continue-panel').scroll_into_view_if_needed()
            page.screenshot(path=str(root/'continue-narrow.png'))
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            contexts[0].close();close();start()
            recovered=fresh();recovered.locator('#continue-list').click();recovered.locator('#continue-choice').select_option(point['id'])
            recovered.locator('#continue-preview').click();expect(recovered.locator('#desk-restore-preview')).to_be_visible(timeout=60000)
            assert recovered.evaluate('ManjuDesk.fingerprint()')!=expected
            recovered.locator('#desk-restore-confirmed').check();recovered.locator('#desk-restore-apply').click()
            expect(recovered.locator('#desk-save-status')).to_contain_text('没有标记独立备份')
            assert recovered.evaluate('ManjuDesk.fingerprint()')==expected
            assert recovered.evaluate('ManjuDesk.view()')==buffers
            assert recovered.evaluate('ManjuDesk.needsSave()')
            for id in ['human-confirmed','exchange-confirmed','flex-confirmed','promotion-confirmed']:
                assert not recovered.locator('#'+id).is_checked()
            assert recovered.locator('#quality-only').is_checked()
            played=recovered.evaluate('''async()=>{let n=0;for(const v of document.querySelectorAll('video')){if(v.src?.startsWith('blob:')){await v.play();await new Promise(r=>setTimeout(r,120));if(v.currentTime<=0)throw Error('video not progressing');v.pause();n++;}}return n;}''')
            assert played>=2
            recovered.set_viewport_size({'width':390,'height':844});recovered.locator('#continue-list').click();recovered.locator('#continue-panel').scroll_into_view_if_needed()
            recovered.screenshot(path=str(root/'restored-narrow.png'));assert recovered.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            with recovered.expect_download(timeout=30000) as dl:recovered.locator('#continue-download').click()
            output=root/'RECOVERED_DESK.zip';dl.value.save_as(output)
            assert output.read_bytes()==local_file.read_bytes();verify_desk(output)
            legacy=fresh(True);import_desk(legacy,output)
            assert legacy.evaluate('ManjuDesk.fingerprint()')==expected
            with legacy.expect_download(timeout=30000) as dl:legacy.locator('#desk-save').click()
            reexport=root/'R16_1_REEXPORTED.zip';dl.value.save_as(reexport);assert reexport.read_bytes()==output.read_bytes()
            # Independently check every original media file, and fully decode videos.
            def media(path):
                with zipfile.ZipFile(path) as desk:
                    with zipfile.ZipFile(io.BytesIO(desk.read('STUDIO.zip'))) as studio:
                        doc=json.loads(studio.read('STUDIO.json'))
                        return {m['sha256']:(m,studio.read('media/'+m['sha256'])) for m in doc['media']}
            prior,after=media(args.example),media(output);assert set(prior)==set(after)
            decoded=[];pixels=[]
            for h,(entry,data) in after.items():
                assert data==prior[h][1] and hashlib.sha256(data).hexdigest()==h
                if entry['mime_type'].startswith('video/'):
                    target=root/(h[:12]+'.mp4');target.write_bytes(data)
                    result=subprocess.run(['ffmpeg','-v','error','-i',str(target),'-f','null','-'],capture_output=True,timeout=45)
                    assert result.returncode==0,result.stderr.decode(errors='replace');decoded.append(h)
                elif data.startswith(b'\x89PNG\r\n\x1a\n'):
                    import struct
                    pixels.append(list(struct.unpack('>II',data[16:24])))
            assert [1920,1080] in pixels
            assert sha(args.example)==before
            assert not errors,errors
            result={'ok':True,'version':manju.__version__,'runtime_module':manju.__file__,
                'transport':'isolated Chromium document with test-only fetch adapter to genuine HTTP service',
                'native_navigation_verified':False,'windows_verified':False,'service_restart_verified':True,
                'media_files_preserved':len(after),'video_elements_played':played,'videos_fully_decoded':len(decoded),
                'png_pixels':pixels,'desktop_buffers_preserved':True,'legacy_r16_1_reexport_identical':True,
                'independent_backup_not_claimed':True,'source_example_unchanged':True,
                'desk_sha256':sha(output),'javascript_errors':errors,'commercial_api_called':False}
            (root/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False,indent=2))
            browser.close()
    finally:
        for server,thread in servers:
            if thread.is_alive():server.shutdown();server.server_close();thread.join(3)


if __name__=='__main__':main()

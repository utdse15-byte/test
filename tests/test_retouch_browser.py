from pathlib import Path
from hashlib import sha256
import json
import subprocess
import zipfile

import pytest
from playwright.sync_api import expect
from manju.authoring.retouch import verify_kit
from manju.authoring.director import verify_director_bundle
from tests.test_rebuilt_r5_workbench import browser,page
from tests.test_rebuilt_r12_director import png


@pytest.fixture(scope='module')
def video(tmp_path_factory):
    root=tmp_path_factory.mktemp('retouch-media');p=root/'运动.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                    'testsrc2=size=640x360:rate=12:duration=2','-c:v','libx264','-pix_fmt','yuv420p',str(p)],check=True,timeout=30)
    return p


def setup(page,video):
    page.locator('#prompt').fill('独立镜头新稿，精修操作不得覆盖。')
    page.locator('#director-source').set_input_files(video)
    page.wait_for_function('()=>ManjuDirector.state.entry!==null&&!ManjuDirector.state.busy')
    page.wait_for_function('()=>document.querySelector("#director-video").readyState>=2')
    for seconds in (0.25,1.25):
        page.evaluate('s=>{let v=document.querySelector("#director-video");v.pause();v.currentTime=s;}',seconds)
        page.wait_for_function('()=>!document.querySelector("#director-video").seeking&&document.querySelector("#director-video").readyState>=2')
        page.locator('#director-capture').click()
        page.wait_for_function('()=>!ManjuDirector.state.busy')
    for a in ('A01','A02'):page.get_by_label(a+' 目标外观',exact=True).fill('只改变背景光线，保留构图、人物动作和文字。')
    page.locator('#retouch-section').evaluate('e=>e.open=true')
    page.locator('#retouch-route').select_option('openai-sunburst-20260908')


def export(page,root):
    with page.expect_download(timeout=15000) as d:page.locator('#retouch-export').click()
    path=root/'kit.zip';d.value.save_as(path);assert verify_kit(path)['ok']
    with zipfile.ZipFile(path) as z:task=json.loads(z.read('TASK.json'));(root/'TASK.json').write_bytes(z.read('TASK.json'))
    page.locator('#retouch-task').set_input_files(root/'TASK.json')
    expect(page.locator('#retouch-status')).to_contain_text('已载入')
    return task,path


def output(root,task,index=0,w=1920,h=1080):
    p=root/task['jobs'][index]['output_name'];p.write_bytes(png(w,h,52+index));return p


def test_full_export_preview_return_save_preserves_pixels(page,video,tmp_path):
    setup(page,video);before=page.evaluate('ManjuWorkbench.getRequest()');sourcehash=sha256(video.read_bytes()).hexdigest()
    task,path=export(page,tmp_path);out=output(tmp_path,task)
    page.locator('#retouch-outputs').set_input_files(out);page.locator('#retouch-preview').click()
    expect(page.locator('#retouch-return-preview')).to_be_visible()
    assert page.evaluate('ManjuDirector.state.anchors[0].guide') is None
    page.locator('#retouch-confirm').check();page.locator('#retouch-apply').click()
    expect(page.locator('#retouch-status')).to_contain_text('已接回 1')
    assert page.evaluate('ManjuDirector.state.anchors[0].guide.width')==1920
    assert page.evaluate('ManjuDirector.state.anchors[1].guide') is None
    assert before==page.evaluate('ManjuWorkbench.getRequest()')
    assert sourcehash==sha256(video.read_bytes()).hexdigest()
    with page.expect_download() as d:page.locator('#director-export').click()
    saved=tmp_path/'DIRECTOR.zip';d.value.save_as(saved);assert verify_director_bundle(saved)['ok']
    with zipfile.ZipFile(saved) as z:
        assert z.read(next(n for n in z.namelist() if n.startswith('guides/')))==out.read_bytes()
    # The same task can bring back the other anchor after partial completion.
    second=output(tmp_path,task,1)
    page.locator('#retouch-outputs').set_input_files(second);page.locator('#retouch-preview').click()
    expect(page.locator('#retouch-return-preview')).to_be_visible()
    page.locator('#retouch-confirm').check();page.locator('#retouch-apply').click()
    expect(page.locator('#retouch-status')).to_contain_text('已接回 1')
    assert all(a['guide'] for a in page.evaluate('ManjuDirector.state.anchors'))


def test_new_text_invalidates_preview_and_blocks_stale_return(page,video,tmp_path):
    setup(page,video);task,_=export(page,tmp_path);out=output(tmp_path,task)
    page.locator('#retouch-outputs').set_input_files(out);page.locator('#retouch-preview').click()
    expect(page.locator('#retouch-return-preview')).to_be_visible()
    page.get_by_label('A01 目标外观',exact=True).fill('后来写的新要求')
    expect(page.locator('#retouch-apply')).to_be_disabled()
    page.locator('#retouch-preview').click();expect(page.locator('#retouch-status')).to_contain_text('已修改')
    assert page.evaluate('ManjuDirector.state.anchors[0].guide') is None


@pytest.mark.parametrize('bad',['wrong_name','wrong_aspect','truncated'])
def test_bad_results_never_replace_any_guide(page,video,tmp_path,bad):
    setup(page,video);task,_=export(page,tmp_path);out=output(tmp_path,task,w=100 if bad=='wrong_aspect' else 1920)
    if bad=='wrong_name':out=out.rename(tmp_path/'wrong.png')
    if bad=='truncated':out.write_bytes(out.read_bytes()[:40])
    page.locator('#retouch-outputs').set_input_files(out);page.locator('#retouch-preview').click()
    expect(page.locator('#retouch-status')).to_have_class('hint reason')
    assert all(a['guide'] is None for a in page.evaluate('ManjuDirector.state.anchors'))
    expect(page.locator('#retouch-apply')).to_be_disabled()


def test_invalid_task_retains_previous_valid_one(page,video,tmp_path):
    setup(page,video);task,_=export(page,tmp_path)
    p=tmp_path/'bad.json';p.write_text('{"schema_id":1,"schema_id":2}',encoding='utf-8')
    page.locator('#retouch-task').set_input_files(p)
    expect(page.locator('#retouch-status')).to_have_class('hint reason')
    assert page.evaluate('ManjuRetouch.state.task.task_sha256')==task['task_sha256']


def test_narrow_preview_no_horizontal_overflow(page,video,tmp_path):
    setup(page,video);task,_=export(page,tmp_path);out=output(tmp_path,task)
    page.locator('#retouch-outputs').set_input_files(out);page.locator('#retouch-preview').click()
    expect(page.locator('#retouch-return-preview')).to_be_visible()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')


def test_home_intake_routes_task_without_touching_director(page,video,tmp_path):
    setup(page,video);task,_=export(page,tmp_path)
    before=page.evaluate('ManjuDirector.plan()')
    page.locator('#intake-files').set_input_files(tmp_path/'TASK.json')
    expect(page.locator('#intake-preview')).to_be_visible()
    page.locator('#intake-open').click()
    expect(page.locator('#retouch-status')).to_contain_text('已载入')
    assert page.evaluate('ManjuDirector.plan()')==before


def test_one_bad_of_two_files_is_atomic(page,video,tmp_path):
    setup(page,video);task,_=export(page,tmp_path)
    a=output(tmp_path,task);b=output(tmp_path,task,1,w=100,h=100)
    page.locator('#retouch-outputs').set_input_files([a,b]);page.locator('#retouch-preview').click()
    expect(page.locator('#retouch-status')).to_contain_text('画幅不符')
    assert all(x['guide'] is None for x in page.evaluate('ManjuDirector.state.anchors'))

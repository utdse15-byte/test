from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import struct
import subprocess
import zipfile
import zlib

import pytest
from typer.testing import CliRunner

from manju.authoring.core import canonical, digest, file_digest, AuthoringError
from manju.authoring.director import (DirectorPlan, director_brief, media_records,
    png_dimensions, verify_director_bundle, write_director_bundle)
from manju.authoring.cli import app
from tests.test_rebuilt_r5_workbench import browser, page


def png(width=320,height=180,value=90):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress((b'\0'+bytes([value,180,210])*width)*height))+chunk(b'IEND',b''))


def fake_plan():
    image=png(); guide=png(value=110);source=b'unit-test-video-placeholder-not-decode-evidence'
    def raster(data,name):return dict(sha256=sha256(data).hexdigest(),bytes=len(data),width=320,height=180,filename=name)
    body=dict(schema_id='manju.director-plan/v1',shot_id='合成结构测试',source=dict(sha256=sha256(source).hexdigest(),bytes=len(source),filename='原片.mp4',duration_ms=3000,width=320,height=180,media_check='browser_metadata'),source_role='motion_timing_camera_reference_not_final_look',anchors=[dict(id='A01',source_time_ms=500,frame=raster(image,'frame.png'),guide=raster(guide,'目标.png'),target='衣服变蓝，手部动作不变',capture_method='browser_canvas_sdr_reference',time_basis='presentation_time_not_certified_frame_index')],preserve=['手部动作'],change=['服装颜色'],audio_policy='preserve_original',delivery_status='draft_reference_only',execution_authorized=False,automatic_selection=False)
    body['plan_sha256']=digest(body)
    return DirectorPlan.model_validate(body),source,image,guide


def recompute(body):
    body.pop('plan_sha256',None);body['plan_sha256']=digest(body);return body


@pytest.fixture
def package(tmp_path):
    plan,source,frame,guide=fake_plan()
    paths={}
    for name,rec in media_records(plan).items():
        data=source if name.startswith('source/') else (frame if name.startswith('frames/') else guide)
        path=tmp_path/Path(name).name;path.write_bytes(data);paths[name]=path
    archive=tmp_path/'director.zip';write_director_bundle(plan,paths,archive)
    return plan,paths,archive


def rewrite(source,target,edit):
    with zipfile.ZipFile(source) as z:entries={n:z.read(n) for n in z.namelist()}
    edit(entries)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_STORED) as z:
        for n,b in entries.items():z.writestr(n,b)
    return target


def test_package_closed_and_cli_read_only(package):
    plan,media,path=package;before=file_digest(path)
    result=verify_director_bundle(path)
    assert result['ok'] and result['guides']==1 and result['anchors']==1
    assert not result['png_pixels_fully_decoded'] and not result['capture_time_frame_accuracy_verified']
    assert not result['source_metadata_independently_reprobed']
    assert not result['execution_authorized'] and not result['automatic_selection']
    cli=CliRunner().invoke(app,['director-verify',str(path)])
    assert cli.exit_code==0,cli.stdout
    assert file_digest(path)==before
    with pytest.raises(FileExistsError):write_director_bundle(plan,media,path)
    assert file_digest(path)==before


@pytest.mark.parametrize('mutator',[
    lambda p:p.update(automatic_selection=True),
    lambda p:p.update(execution_authorized=True),
    lambda p:p.update(delivery_status='approved'),
    lambda p:p.update(source_role='all_purpose_reference'),
    lambda p:p.update(change=['手部动作']),
    lambda p:p['anchors'][0].update(source_time_ms=3000),
    lambda p:p['anchors'][0].update(source_time_ms=-1),
    lambda p:p['anchors'][0].update(source_time_ms=True),
    lambda p:p['anchors'][0].update(time_basis='exact_frame_id'),
    lambda p:p['anchors'][0].update(capture_method='hdr_master'),
    lambda p:p['anchors'][0]['guide'].update(width=321),
    lambda p:p['anchors'][0]['frame'].update(filename='../x.png'),
    lambda p:p['anchors'][0]['frame'].update(filename='x.jpg'),
    lambda p:p['anchors'][0]['frame'].update(bytes=34*1024*1024),
    lambda p:p['anchors'].append(deepcopy(p['anchors'][0])),
    lambda p:p.update(anchors=[]),
    lambda p:p['source'].update(media_check='hash_only'),
    lambda p:p.update(shot_id='  未裁边  '),
])
def test_semantic_corruption_rejected_even_with_new_hash(mutator):
    p=fake_plan()[0].model_dump(mode='json');mutator(p);recompute(p)
    with pytest.raises(ValueError):DirectorPlan.model_validate(p)


def test_incomplete_target_is_saved_as_draft_not_approval():
    p=fake_plan()[0].model_dump(mode='json');p['anchors'][0].update(guide=None,target='');p['preserve']=[];p['change']=[];recompute(p)
    result=DirectorPlan.model_validate(p)
    assert result.delivery_status=='draft_reference_only'
    assert '尚未填写' in director_brief(result) and '未绑定' in director_brief(result)


@pytest.mark.parametrize('edit',[
    lambda e:e.update({'../escape.txt':b'no'}),
    lambda e:e.update({'surprise.txt':b'no'}),
    lambda e:e.__setitem__('BRIEF.md',b'changed'),
    lambda e:e.__delitem__('PLAN.json'),
    lambda e:e.__delitem__(next(n for n in e if n.startswith('frames/'))),
    lambda e:e.__setitem__(next(n for n in e if n.startswith('source/')),b'changed'),
    lambda e:e.__setitem__('MANIFEST.json',b'{"schema_id":"x","schema_id":"y","files":{}}'),
])
def test_inventory_corruption_rejected(package,tmp_path,edit):
    target=rewrite(package[2],tmp_path/'bad.zip',edit)
    with pytest.raises((ValueError,AuthoringError,KeyError)):verify_director_bundle(target)


@pytest.mark.parametrize('variant',['prefix','trailer','duplicate','compressed','symlink'])
def test_zip_attack_boundaries(package,tmp_path,variant):
    source=package[2];target=tmp_path/'bad.zip'
    if variant=='prefix':target.write_bytes(b'prefix'+source.read_bytes())
    elif variant=='trailer':target.write_bytes(source.read_bytes()+b'garbage')
    else:
        with zipfile.ZipFile(source) as z:entries=[(n,z.read(n)) for n in z.namelist()]
        with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED if variant=='compressed' else zipfile.ZIP_STORED) as z:
            for name,data in entries:
                if variant=='symlink' and name.startswith('frames/'):
                    info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=0o120777<<16;z.writestr(info,data)
                else:z.writestr(name,data)
            if variant=='duplicate':
                with pytest.warns(UserWarning):z.writestr(entries[0][0],entries[0][1])
    with pytest.raises((ValueError,AuthoringError)):verify_director_bundle(target)


@pytest.mark.parametrize('mutation',[lambda b:b+b'x',lambda b:b[:-1],lambda b:b[:20]+b'x'+b[21:],lambda b:b'no'])
def test_png_structure_corruption(mutation):
    with pytest.raises(AuthoringError):png_dimensions(mutation(png()))


def test_browser_python_plan_and_brief_parity(page):
    p=fake_plan()[0]
    assert page.evaluate('p=>ManjuDirector.normalizeDirector(p)',p.model_dump(mode='json'))==p.model_dump(mode='json')
    assert page.evaluate('p=>ManjuDirector.brief(p)',p.model_dump(mode='json'))==director_brief(p)


@pytest.fixture(scope='module')
def video(tmp_path_factory):
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg unavailable for real video fixture')
    root=tmp_path_factory.mktemp('r12-media');path=root/'原片_合成.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=s=320x180:r=24','-t','3','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True,timeout=15)
    return path


def load_video(page,video):
    page.locator('#director-source').set_input_files(video)
    page.wait_for_function('()=>ManjuDirector.state.source!==null&&!ManjuDirector.state.busy')
    page.wait_for_function('()=>document.getElementById("director-video").readyState>=2')


def capture_at(page,seconds):
    page.evaluate('(s)=>{const v=document.getElementById("director-video");v.pause();v.currentTime=s;}',seconds)
    page.wait_for_function('()=>!document.getElementById("director-video").seeking&&document.getElementById("director-video").readyState>=2')
    count=page.evaluate('ManjuDirector.state.anchors.length')
    page.locator('#director-capture').click()
    page.wait_for_function('(n)=>ManjuDirector.state.anchors.length===n+1&&!ManjuDirector.state.busy',arg=count)


def test_real_capture_guide_download_and_restoration(page,video,tmp_path):
    before=file_digest(video);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    load_video(page,video);capture_at(page,0.5);capture_at(page,1.5)
    page.get_by_label('A01 目标外观',exact=True).fill('蓝色衣服，保持原来的表演；这是合成测试。')
    guide=tmp_path/'目标.png';guide.write_bytes(png(640,360,value=120))
    page.get_by_label('A01 目标 PNG',exact=True).set_input_files(guide)
    page.wait_for_function('()=>ManjuDirector.state.anchors[0].guide!==null&&!ManjuDirector.state.busy')
    page.locator('#director-preserve').fill('运动时序')
    page.locator('#director-change').fill('外观')
    with page.expect_download(timeout=20000) as info:page.locator('#director-export').click()
    archive=tmp_path/'actual.zip';info.value.save_as(archive)
    result=verify_director_bundle(archive);assert result['anchors']==2 and result['guides']==1
    with zipfile.ZipFile(archive) as z:
        guide_name=next(n for n in z.namelist() if n.startswith('guides/'))
        assert z.read(guide_name)==guide.read_bytes()
        assert png_dimensions(z.read(guide_name))==(640,360)
    assert file_digest(video)==before
    with zipfile.ZipFile(archive) as z:
        plan=json.loads(z.read('PLAN.json'))
        for a in plan['anchors']:
            dims=png_dimensions(z.read('frames/'+a['frame']['sha256']+'.png'))
            assert dims==(320,180)
    page.locator('#prompt').fill('不允许导演恢复覆盖的新任务')
    original=page.evaluate('ManjuWorkbench.getRequest()')
    page.on('dialog',lambda d:d.accept())
    page.locator('#director-reset').click()
    page.locator('#director-import').set_input_files(archive)
    page.wait_for_function('()=>ManjuDirector.state.anchors.length===2&&!ManjuDirector.state.busy')
    assert page.evaluate('ManjuWorkbench.getRequest()')==original
    page.evaluate('()=>document.getElementById("director-video").play()')
    page.wait_for_function('()=>document.getElementById("director-video").currentTime>0.1')
    assert page.get_by_label('A01 目标外观',exact=True).input_value().startswith('蓝色')
    with page.expect_download() as info:page.locator('#director-export').click()
    restored=tmp_path/'restored.zip';info.value.save_as(restored)
    assert restored.read_bytes()==archive.read_bytes()
    assert not errors
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not page.locator('#director-anchors').inner_text().startswith('null')


def test_wrong_aspect_guide_rejected_without_changing_anchor(page,video,tmp_path):
    load_video(page,video);capture_at(page,0.5)
    wrong=tmp_path/'wrong.png';wrong.write_bytes(png(318,180))
    before=page.evaluate('ManjuDirector.state.anchors')
    page.get_by_label('A01 目标 PNG',exact=True).set_input_files(wrong)
    page.wait_for_function('()=>document.getElementById("status").textContent.includes("画幅相同")')
    assert page.evaluate('ManjuDirector.state.anchors')==before
    assert not page.evaluate('ManjuDirector.state.busy')


def test_duplicate_capture_does_not_replace_old_frame(page,video):
    load_video(page,video);capture_at(page,0.5)
    before=page.evaluate('ManjuDirector.state.anchors')
    page.locator('#director-capture').click()
    page.wait_for_function('()=>document.getElementById("status").textContent.includes("时刻已存在")')
    assert page.evaluate('ManjuDirector.state.anchors')==before


def test_browser_rechecks_source_metadata_before_replacement(page,video,tmp_path):
    from playwright.sync_api import expect
    # A internally consistent hash inventory is NOT enough to certify metadata.
    base,_,frame,guide=fake_plan();body=base.model_dump(mode='json')
    body['source'].update(sha256=file_digest(video),bytes=video.stat().st_size,duration_ms=8000)
    recompute(body);plan=DirectorPlan.model_validate(body)
    paths={}
    for name in media_records(plan):
        if name.startswith('source/'):paths[name]=video
        else:
            p=tmp_path/Path(name).name;p.write_bytes(frame if name.startswith('frames/') else guide);paths[name]=p
    target=tmp_path/'lying-metadata.zip';write_director_bundle(plan,paths,target)
    assert verify_director_bundle(target)['source_metadata_independently_reprobed'] is False
    page.locator('#director-shot').fill('保留这个未完成镜头')
    page.locator('#director-import').set_input_files(target)
    expect(page.locator('#status')).to_contain_text('原片实际元数据')
    assert page.evaluate('ManjuDirector.state.source') is None
    assert page.locator('#director-shot').input_value()=='保留这个未完成镜头'
    assert not page.evaluate('ManjuDirector.state.busy')


def test_independent_director_context_and_save_warning(page):
    assert '导演区' in page.locator('#workspace-status').inner_text()
    page.locator('#prompt').fill('主镜头不丢失')
    original=page.evaluate('ManjuWorkbench.getRequest()')
    page.locator('#director-shot').fill('独立导演片段')
    assert page.evaluate('ManjuWorkbench.getRequest()')==original
    assert page.evaluate('ManjuDirector.state.dirty')

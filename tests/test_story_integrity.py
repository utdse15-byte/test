from copy import deepcopy
from hashlib import sha256
import io
import json
import zipfile
import pytest
from manju.authoring.core import canonical,digest,AuthoringError
from manju.authoring.story import Story,Compiled,verify_brief_kit
from tests.test_story_sources import sample,pin
from tests.test_rebuilt_r5_workbench import browser,page


def make_kit(path,mutate=None):
    d=sample();b=pin(d);raw=b'original media content'
    h=sha256(raw).hexdigest();b['content']['scene']['references']=[dict(id='R1',sha256=h,bytes=len(raw),filename='original.mp4',role='performance',inherit='动作',exclude='身份',time_range='')];b['content_sha256']=digest(b['content'])
    content={'BRIEF.json':canonical(b),'README.md':'这是原素材交接，不是完整备份。'.encode(),'media/'+h+'.mp4':raw}
    inv={'schema_id':'manju.story-brief-manifest/v1','files':{k:sha256(v).hexdigest() for k,v in content.items()}}
    content['MANIFEST.json']=canonical(inv)
    if mutate:mutate(content,b)
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_STORED) as z:
        for k,v in content.items():z.writestr(k,v)
    return path


def test_actual_closed_brief_without_execution(tmp_path):
    p=make_kit(tmp_path/'a.zip');before=p.read_bytes();r=verify_brief_kit(p)
    assert r['ok'] and r['media_files']==1 and r['reference_roles']==1
    assert not r['media_decoded'] and not r['current_notebook_checked'] and not r['creative_approval']
    assert p.read_bytes()==before


@pytest.mark.parametrize('case',['altered','missing','extra','traversal','truncated','wrong-size','wrong-sha','revised-manifest-with-extra'])
def test_brief_refuses_invalid_closure(tmp_path,case):
    def change(c,b):
        media=next(k for k in c if k.startswith('media/'))
        if case=='altered':c[media]=b'changed'
        elif case=='missing':del c[media]
        elif case=='extra':c['unknown.txt']=b'x'
        elif case=='traversal':c['../unknown']=b'x'
        elif case=='wrong-size':b['content']['scene']['references'][0]['bytes']+=1
        elif case=='wrong-sha':b['content']['scene']['references'][0]['sha256']='a'*64
        elif case=='revised-manifest-with-extra':
            c['media/'+('a'*64)+'.mp4']=b'x'
        if case in ['wrong-size','wrong-sha','revised-manifest-with-extra']:
            b['content_sha256']=digest(b['content']);c['BRIEF.json']=canonical(b)
            c['MANIFEST.json']=canonical({'schema_id':'manju.story-brief-manifest/v1','files':{k:sha256(v).hexdigest() for k,v in c.items() if k!='MANIFEST.json'}})
    p=make_kit(tmp_path/'a.zip',change)
    if case=='truncated':p.write_bytes(p.read_bytes()[:-10])
    with pytest.raises((ValueError,zipfile.BadZipFile)):verify_brief_kit(p)


@pytest.mark.parametrize('case',['unknown-form','leaked-ending','wrong-order','invented-history','bad-withheld','nonstring-title','extra-source','outside-source'])
def test_frozen_body_rejected_in_both_runtimes(page,case):
    d=sample();b=pin(d);c=b['content']
    if case=='unknown-form':c['form']='all_templates'
    elif case=='leaked-ending':c['ending']='泄露结局'
    elif case=='wrong-order':c['order_prefix']=['S30']
    elif case=='invented-history':c['history']=[{'id':'S99','episode':'','title':'','action':'','dialogue':'','after':''}]
    elif case=='bad-withheld':c['withheld'][0]['text']='不应泄露的计划'
    elif case=='nonstring-title':c['project_title']=2
    elif case=='extra-source':c['sources'].append(deepcopy(c['sources'][0]))
    elif case=='outside-source':c['sources'][0]['scope']='author'
    b['content_sha256']=digest(c)
    with pytest.raises(ValueError):Story.model_validate(d)
    with pytest.raises(Exception):page.evaluate('d=>ManjuStory.normalize(d)',d)


def test_optional_notebook_warns_about_legacy_backup_scope(page):
    page.evaluate('d=>ManjuStory.install(d)',sample())
    assert not page.locator('#story-home-warning').evaluate('e=>e.hidden')
    assert '不含故事资料' in page.locator('#story-home-warning').inner_text()
    page.evaluate('ManjuStory.install(null)');assert page.locator('#story-home-warning').evaluate('e=>e.hidden')


def test_local_recovery_survives_service_store_reopen_with_story_v2(tmp_path):
    from tests.test_local_continuation import M,put
    from tests.test_rebuilt_r16_user_desk import write_desk
    d=sample();pin(d);archive,_,_=write_desk(tmp_path,edit=lambda doc:doc.update(schema_id='manju.desk-session/v2',story=d))
    store=M.RecoveryStore(tmp_path/'recover');r=put(store,archive)['point']
    again=M.RecoveryStore(store.root);record,stream=again.load(r['id'])
    with stream: assert stream.read()==archive.read_bytes()
    assert again.inspect(r['id'],r['sha256'])['ok']


def test_active_story_tab_remains_legible_while_hovered(page):
    page.evaluate('d=>ManjuStory.install(d)',sample());page.locator('[data-story-tab=sources]').click()
    tab=page.locator('[data-story-tab=sources]');tab.hover()
    # Wait for the existing short CSS transition to reach its actual target.
    page.wait_for_function('()=>getComputedStyle(document.querySelector("[data-story-tab=sources]")).backgroundColor==="rgb(24, 101, 77)"')
    assert tab.evaluate('e=>getComputedStyle(e).color')=='rgb(255, 255, 255)'


def test_story_title_search_summary_does_not_modify_older_payload(tmp_path):
    from tests.test_local_continuation import M,put,contents
    from tests.test_rebuilt_r16_user_desk import write_desk
    d=sample();pin(d);a,_,_=write_desk(tmp_path,edit=lambda x:x.update(schema_id='manju.desk-session/v2',story=d))
    s=M.RecoveryStore(tmp_path/'recover');r=put(s,a)['point'];before=contents(s.root)
    result=s.inspect(r['id'],r['sha256'])['summary']
    assert result['story_summary']=={'title':d['title'],'scenes':3,'sources':4,'briefs':1}
    assert contents(s.root)==before

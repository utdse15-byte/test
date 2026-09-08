"""Explicit raster targets avoid misreading marketing resolution labels."""
import pytest
from manju.authoring.core import Request, digest
from manju.authoring.returns import return_report, raster_report
from manju.review.core import Candidate
from tests.test_rebuilt_r5_workbench import browser,page


def request():return Request(shot_id='wide',task='create',prompt='一个连续镜头',duration_s=6,resolution='720p',aspect_ratio='21:9')
def candidate(w=1584,h=672):return Candidate(sha256='a'*64,bytes=100,filename='wide.mp4',duration_ms=6000,width=w,height=h,media_check='ffprobe')


def test_known_branded_720p_does_not_fail_explicit_raster():
    r=request();old=return_report(r,[candidate()]);new=raster_report(r,[candidate()],(1584,672))
    assert old['items'][0]['checks'][-1]['state']=='mismatch'
    assert new['items'][0]['status']=='metadata_matches_requested_checks'
    assert new['schema_id']=='manju.return-preflight/v2' and old['schema_id']=='manju.return-preflight/v1'
    assert new['request_sha256']==old['request_sha256']==digest(r)
    assert not new['policy']['vendor_origin_inferred'] and not new['automatic_approval']


@pytest.mark.parametrize('width,height',[(1584,672),(1280,720),(1582,672),(672,1584)])
def test_new_report_browser_python_parity(page,width,height):
    r=request();c=candidate(width,height);expected=raster_report(r,[c],(1584,672))
    actual=page.evaluate('x=>ManjuReturns.rasterReport(x.r,[x.c],[1584,672])',{'r':r.model_dump(mode='json'),'c':c.model_dump(mode='json')})
    assert actual==expected


@pytest.mark.parametrize('pixels',[(0,1),(1,32769),(True,720),(1584.0,672),(720,),('1584',672)])
def test_invalid_pixels_not_accepted(pixels):
    with pytest.raises(ValueError):raster_report(request(),[candidate()],pixels)


def test_pixel_template_is_an_explicit_user_action(page):
    page.locator('#prompt').fill('宽画幅镜头');page.locator('#ratio').select_option('21:9')
    before=page.evaluate('ManjuWorkbench.getRequest()')
    assert page.locator('#return-width').input_value()==''
    page.locator('#return-section summary').click();page.locator('#return-runway-raster').click()
    assert page.locator('#return-width').input_value()=='1584'
    assert page.locator('#return-height').input_value()=='672'
    assert page.evaluate('ManjuWorkbench.getRequest()')==before

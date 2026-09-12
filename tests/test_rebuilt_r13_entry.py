"""User-facing recovery entry points must refer to the real current release."""
from pathlib import Path
import json
from tests.test_rebuilt_r5_workbench import browser, page

ROOT = Path(__file__).resolve().parents[1]


def test_primary_state_links_latest_snapshot():
    latest = sorted(ROOT.glob('PROJECT_STATE_*.md'))[-1]
    assert latest.name in (ROOT/'STATE.md').read_text(encoding='utf-8')


def test_studio_has_one_complete_save_and_separate_download_verification(page):
    assert page.locator('#export-studio').count() == 1
    assert page.locator('#verify-studio-download').count() == 1
    assert page.locator('#import-studio').count() == 1
    assert page.locator('#quality-only').is_checked()


def test_personal_navigation_reaches_existing_workspaces(page):
    for target in ('workspace-section', 'review-section', 'repair-section', 'director-section'):
        assert page.locator(f'#studio-home a[href="#{target}"]').count() == 1


def test_handoff_and_readme_do_not_send_owner_back_to_r7():
    latest = sorted(ROOT.glob('PROJECT_STATE_*.md'))[-1]
    assert latest.name in (ROOT/'FINAL_HANDOFF.md').read_text(encoding='utf-8')
    stage=json.loads((ROOT/'DELIVERY_VERSION.json').read_text(encoding='utf-8'))['stage']
    assert f'Manju {stage}' in (ROOT/'FINAL_HANDOFF.md').read_text(encoding='utf-8')
    text=(ROOT/'tools/delivery/QUICKSTART_ZH.md').read_text(encoding='utf-8')
    assert f'Manju {stage}' in text and '只需保存一份' in text
    assert '总备份仅覆盖此离线页面' in text
    assert 'tools/delivery/QUICKSTART_ZH.md' in (ROOT/'README.md').read_text(encoding='utf-8')

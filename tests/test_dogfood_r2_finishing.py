"""Real DOM regressions for first-use finishing forms (no network/providers)."""
from __future__ import annotations

import os
import re
import shutil

import pytest

from manju.gui import pages_t


@pytest.fixture
def browser_page():
    api = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("MANJU_DEV_CHROMIUM") or shutil.which("chromium") or shutil.which("msedge")
    if not executable:
        pytest.skip("A local Chromium/Edge is required; no browser is downloaded")
    with api.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=executable, headless=True,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()
        yield page
        browser.close()


def mount(page, html, script):
    """Execute the actual renderer/script; transport is stubbed, never called."""
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.S)
    html = re.sub(r'<link[^>]*>', '', html)
    page.set_content(html, wait_until="domcontentloaded")
    page.evaluate("window.toast = (message) => {window.lastToast = message;}")
    if script:
        page.add_script_tag(content=script)


@pytest.mark.parametrize("path,button,row,delete,defaults", [
    ("/mixer", "#sfx-add", "#sfx-body .sfx-row", "[data-act=sfx-del]",
     {".sfx-at": "", ".sfx-offset": "0", ".sfx-gain": "-6"}),
    ("/packaging", "#ic-add", "#ic-body .ic-row", "[data-act=ic-del]",
     {".ic-text": "", ".ic-at": "", ".ic-offset": "0", ".ic-duration": "1500",
      ".ic-kind": "chapter", ".ic-template": "chapter"}),
])
def test_empty_list_add_edit_delete_add_uses_pristine_defaults(
    tmp_project, browser_page, path, button, row, delete, defaults
):
    page = browser_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    mount(page, pages_t.render(path, tmp_project, "test-token", {}), pages_t.render_pages_t_js())
    assert page.locator(row).count() == 0
    page.locator(button).click()
    assert page.locator(row).count() == 1, "Adding the FIRST item must not need an existing prototype"
    first = page.locator(row).first
    for selector, value in defaults.items():
        assert first.locator(selector).input_value() == value
    first.locator("input[type=text]").first.fill("中文草稿 <script>not executable</script>")
    if path == "/packaging":
        first.locator(".ic-kind").select_option("role")
        first.locator(".ic-template").select_option("caption")
    page.locator(button).click()
    assert page.locator(row).count() == 2
    for selector, value in defaults.items():
        assert page.locator(row).last.locator(selector).input_value() == value
    page.locator(row).first.locator(delete).click()
    page.locator(row).first.locator(delete).click()
    assert page.locator(row).count() == 0
    page.locator(button).click()
    assert page.locator(row).count() == 1
    for selector, value in defaults.items():
        assert page.locator(row).first.locator(selector).input_value() == value
    assert not errors

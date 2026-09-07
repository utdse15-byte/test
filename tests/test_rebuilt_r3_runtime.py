"""R3 reconstruction: process ownership, publication and partial-result contracts."""
from __future__ import annotations

import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
import zlib
from types import SimpleNamespace

import pytest

from manju.media import ffmpeg as ff
from manju.media import html_card as hc
from manju.providers.base import GenerationRequest
from manju.providers.caption_card import CaptionCardProvider
from manju.providers.kenburns import KenburnsProvider


def png(width=8, height=8):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + b'\x88\x66\x44' * width) * height)) + chunk(b'IEND', b''))


def request(project, shot, check=None, **kw):
    return GenerationRequest(project=project, shot=shot, bible=project.load_bible(),
                             spec_hash='test', duration_ms=400, should_cancel=check, **kw)


def test_pre_cancel_never_spawns(monkeypatch):
    monkeypatch.setattr(ff.subprocess, 'Popen', lambda *a, **k: pytest.fail('process started'))
    with pytest.raises(ff.MediaCanceled):
        ff._run_local_process([sys.executable, '-c', 'pass'], check=lambda: True)


def test_nested_empty_scope_preserves_cancellation():
    with ff.cancel_scope(lambda: True), ff.cancel_scope(None):
        with pytest.raises(ff.MediaCanceled):
            ff.check_canceled()
    ff.check_canceled()


def test_log_output_over_pipe_capacity_does_not_deadlock():
    out = ff._run_local_process([sys.executable, '-c', "import sys;sys.stderr.write('x'*262144);print('done')"], timeout=3)
    assert out.returncode == 0 and out.stdout.strip() == 'done'
    assert len(out.stderr) == 65536


def test_runaway_output_fails_and_stops():
    with pytest.raises(ff.MediaError, match='输出超过限制'):
        ff._run_local_process([sys.executable, '-c', "import sys;sys.stdout.write('x'*262144)"],
                              timeout=3, output_limit=32768)


def test_timeout_is_not_cancel():
    with pytest.raises(subprocess.TimeoutExpired):
        ff._run_local_process([sys.executable, '-c', 'import time;time.sleep(20)'], timeout=.1)


def test_cleanup_timeout_never_becomes_successful_cancel(monkeypatch):
    from tests.test_ffmpeg_cancel_unit import FakeProc
    p = FakeProc(survive_terminate=True)
    def stuck(timeout=None):
        raise subprocess.TimeoutExpired('stuck', timeout)
    p.communicate = stuck
    monkeypatch.setattr(ff, '_signal_local_group', lambda *a: None)
    with pytest.raises(ff.MediaCleanupError):
        ff._stop_local_process(p)


def test_cancel_does_not_stop_unrelated_process():
    other = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(15)'])
    ev = threading.Event()
    timer = threading.Timer(.3, ev.set)
    timer.start()
    start = time.monotonic()
    try:
        with pytest.raises(ff.MediaCanceled):
            ff._run_local_process([sys.executable, '-c', 'import time;time.sleep(15)'], check=ev.is_set)
        assert time.monotonic() - start < 5
        assert other.poll() is None
    finally:
        other.kill(); other.wait(timeout=3); timer.join(timeout=1)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process-group integration; Windows needs a real host')
def test_posix_child_stops_even_when_it_ignores_term(tmp_path):
    marker = tmp_path/'child.pid'
    child = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
    parent = f"import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,'-c',{child!r}]);pathlib.Path({str(marker)!r}).write_text(str(p.pid));time.sleep(30)"
    with pytest.raises(ff.MediaCanceled):
        ff._run_local_process([sys.executable, '-c', parent], timeout=5, check=marker.exists)
    pid = int(marker.read_text())
    for _ in range(100):
        stat = Path(f'/proc/{pid}/stat')
        if not stat.exists() or stat.read_text().split()[2] == 'Z':
            break
        time.sleep(.01)
    else:
        pytest.fail('child still executing after cancellation')


@pytest.mark.parametrize('data', [None, b'not a PNG', png()[:-9], png(9, 8), png() + b'junk'])
def test_bad_or_missing_new_screenshot_keeps_old_file(tmp_path, monkeypatch, data):
    dest = tmp_path/'封面.png'; dest.write_bytes(b'old trusted bytes')
    def render(cmd, **kwargs):
        if data is not None:
            Path(next(v.split('=',1)[1] for v in cmd if v.startswith('--screenshot='))).write_bytes(data)
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(hc, '_run_local_process', render)
    with pytest.raises(ff.MediaError):
        hc.render_card_png('测试', dest, width=8, height=8, chromium=Path('fake-browser'))
    assert dest.read_bytes() == b'old trusted bytes'
    assert not list(tmp_path.glob('.*tmp*'))


def test_valid_screenshot_commits_once_with_isolated_profile(tmp_path, monkeypatch):
    profiles = []
    def render(cmd, **kwargs):
        profiles.append(next(v for v in cmd if v.startswith('--user-data-dir=')))
        path = Path(next(v.split('=',1)[1] for v in cmd if v.startswith('--screenshot=')))
        path.write_bytes(png())
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(hc, '_run_local_process', render)
    for n in range(2):
        p = hc.render_card_png('测试', tmp_path/f'{n}.png', width=8, height=8, chromium=Path('fake'))
        assert p.read_bytes() == png()
    assert profiles[0] != profiles[1]


@pytest.mark.parametrize('exc_type', [ff.MediaCanceled, ff.MediaCleanupError])
def test_html_cancel_or_failed_cleanup_never_falls_back(tmp_project, add_shot, monkeypatch, exc_type):
    import manju.media.card as card
    def stop(*a, **k):
        raise exc_type('stop')
    monkeypatch.setattr(hc, 'html_card_video', stop)
    monkeypatch.setattr(card, 'caption_card', lambda *a, **k: pytest.fail('fallback started'))
    with pytest.raises(exc_type):
        CaptionCardProvider().generate(request(tmp_project, add_shot(tmp_project, 'S001')))
    assert not list(tmp_project.takes('S001'))


def test_registry_never_falls_back_after_cancel(tmp_project, add_shot, monkeypatch):
    import manju.providers.registry as reg
    calls = []
    class Stop(CaptionCardProvider):
        def generate(self, req):
            calls.append(self.id)
            raise ff.MediaCanceled('stop')
    provider = Stop()
    monkeypatch.setattr(reg, 'get_provider', lambda name: provider)
    with pytest.raises(ff.MediaCanceled):
        reg.generate_with_fallback(request(tmp_project, add_shot(tmp_project, 'S001')), ['caption_card', 'caption_card'])
    assert len(calls) == 1


def test_completed_candidate_survives_cancel(tmp_project, add_shot, monkeypatch):
    import importlib
    kb = importlib.import_module("manju.media.kenburns")
    image = tmp_project.imports_dir/'首帧.png'; image.write_bytes(png())
    shot = add_shot(tmp_project, 'S001', generation={'provider': 'ffmpeg_kenburns', 'params': {'image': 'media/imports/首帧.png'}})
    ev = threading.Event()
    def generate(image, dest, **kw):
        dest.write_bytes(b'completed candidate fixture'); return dest
    monkeypatch.setattr(kb, 'kenburns', generate)
    provider = KenburnsProvider()
    original = provider._register
    def register(*a, **k):
        t = original(*a, **k); ev.set(); return t
    monkeypatch.setattr(provider, '_register', register)
    with pytest.raises(ff.MediaCanceled) as error:
        provider.generate(request(tmp_project, shot, ev.is_set, candidates=3, params=dict(shot.generation.params)))
    assert len(error.value.completed_takes) == 1
    assert error.value.completed_takes[0].media_path.read_bytes() == b'completed candidate fixture'
    assert len(tmp_project.takes('S001')) == 1
    assert tmp_project.load_shot('S001').status.selected_take is None


def test_cancellation_classification_is_not_failure():
    from manju.core.outcomes import classify_exception, OutcomeCode
    assert classify_exception(ff.MediaCanceled('stop')).code == OutcomeCode.CANCELED
    assert classify_exception(ff.MediaCleanupError('unconfirmed')).code == OutcomeCode.FAILED


def test_failed_windows_tree_cleanup_is_not_canceled(monkeypatch):
    from tests.test_ffmpeg_cancel_unit import FakeProc
    proc = FakeProc()
    monkeypatch.setattr(ff, '_IS_WINDOWS', True)
    monkeypatch.setattr(ff, '_taskkill_tree', lambda p: False)
    with pytest.raises(ff.MediaCleanupError):
        ff._stop_local_process(proc)
    assert proc.returncode is not None  # direct child reaped, tree remains uncertain


def test_build_preserves_partial_takes_and_local_semantics(tmp_project, add_shot, monkeypatch):
    import importlib
    kb = importlib.import_module('manju.media.kenburns')
    from manju.build.graph import run_build
    image = tmp_project.imports_dir/'首帧.png'; image.write_bytes(png())
    add_shot(tmp_project, 'S001', duration=1, generation={
        'provider':'ffmpeg_kenburns', 'candidates':3, 'params':{'image':'media/imports/首帧.png'}})
    ev = threading.Event()
    def generate(image, dest, **kw):
        dest.write_bytes(b'completed candidate'); return dest
    monkeypatch.setattr(kb, 'kenburns', generate)
    orig = KenburnsProvider._register
    def register(self, *a, **k):
        t = orig(self, *a, **k); ev.set(); return t
    monkeypatch.setattr(KenburnsProvider, '_register', register)
    result = run_build(tmp_project, should_cancel=ev.is_set)
    assert result.canceled, result.to_dict()
    assert len(result.generated) == 1
    assert not any('远程任务' in e for e in result.errors)
    assert tmp_project.load_shot('S001').status.selected_take is None
    assert len(tmp_project.takes('S001')) == 1


def test_redo_batch_keeps_partial_takes(tmp_project, add_shot, monkeypatch):
    import importlib
    kb = importlib.import_module('manju.media.kenburns')
    from manju.build.graph import redo_batch
    image = tmp_project.imports_dir/'首帧.png'; image.write_bytes(png())
    add_shot(tmp_project, 'S001', duration=1, generation={
        'provider':'ffmpeg_kenburns', 'candidates':3, 'params':{'image':'media/imports/首帧.png'}})
    ev = threading.Event()
    def generate(image, dest, **kw):
        dest.write_bytes(b'completed candidate'); return dest
    monkeypatch.setattr(kb, 'kenburns', generate)
    orig = KenburnsProvider._register
    def register(self, *a, **k):
        t = orig(self, *a, **k); ev.set(); return t
    monkeypatch.setattr(KenburnsProvider, '_register', register)
    result = redo_batch(tmp_project, shots=['S001'], should_cancel=ev.is_set)
    assert result.canceled and not result.failed
    assert len(result.takes['S001']) == 1 and result.ran == ['S001']

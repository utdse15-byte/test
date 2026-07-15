"""Cycle-30: running non-cancelable jobs show honest UX note; ASR ProviderCanceled."""

from pathlib import Path

from manju.gui import page as page_mod
from manju.providers.asr import GenericAsrProvider
import inspect


def test_running_noncancel_note_in_page() -> None:
    src = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "运行中不可中途取消" in src
    assert 'j.state === "running"' in src


def test_asr_poll_raises_provider_canceled_signature() -> None:
    # cancel path documented via ProviderCanceled import in asr module
    import manju.providers.asr as asr_mod
    src = Path(asr_mod.__file__).read_text(encoding="utf-8")
    assert "ProviderCanceled" in src
    assert "should_cancel" in inspect.signature(GenericAsrProvider.transcribe).parameters

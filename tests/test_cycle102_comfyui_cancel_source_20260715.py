from pathlib import Path

def test_comfyui_source_has_cancel():
    s = Path("src/manju/providers/comfyui.py").read_text(encoding="utf-8")
    assert "ProviderCanceled" in s
    assert "should_cancel" in s

import inspect
from manju.mcp import tools as t

def test_mcp_build_maps_canceled():
    src = inspect.getsource(t._h_build)
    assert "BuildCanceled" in src
    assert 'code="canceled"' in src or "code='canceled'" in src

def test_mcp_redo_maps_canceled():
    src = inspect.getsource(t._h_redo)
    assert "ProviderCanceled" in src
    assert 'code="canceled"' in src or "code='canceled'" in src

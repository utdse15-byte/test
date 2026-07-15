import inspect
from manju.mcp import tools as t

def test_mcp_build_has_waiting_and_canceled():
    src = inspect.getsource(t._h_build)
    assert "waiting_user" in src
    assert "canceled" in src
    assert "WaitingUser" in src
    assert "BuildCanceled" in src

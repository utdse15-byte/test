from pathlib import Path
from manju.providers import generic_cloud as gc

def test_loopback_helper_exported():
    assert hasattr(gc, "_is_loopback_host")
    assert gc._is_loopback_host("127.0.0.1")
    assert not gc._is_loopback_host("example.com")

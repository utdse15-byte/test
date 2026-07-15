from pathlib import Path
import re

def test_continuous_cycle_tests_exist_many():
    files = list(Path("tests").glob("test_cycle*_20260715.py"))
    assert len(files) >= 50, len(files)

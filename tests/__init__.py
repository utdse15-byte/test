"""Package marker so `from tests.… import …` works under BARE `pytest`.

CI runs `pytest -q` (not `python -m pytest`), which does NOT put the repo
root on sys.path; without this file every `from tests.test_mcp import …` /
`from tests.fixtures.make_sample import …` fails at collection — in CI only,
because local runs used `python -m pytest`, which masks it. With the package
marker, pytest's rootdir insertion makes the imports valid in both styles.
"""

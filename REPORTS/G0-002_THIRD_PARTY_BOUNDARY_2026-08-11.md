# G0-002 Third-Party Boundary Evidence

- Baseline HEAD: `ee254b98e58ba4158f60dc049063bb6fd3e78a24`
- Network model/provider calls: 0
- Provider keys read or configured: 0
- Upstream code executed, installed, or copied: no

## Red-to-green test

Command:

```text
py -3.11 -m pytest -q tests/test_three_project_boundaries.py
```

Initial exit: 1. The new snapshot test failed because
`docs/architecture/THREE_PROJECT_UPSTREAM_SNAPSHOT.yaml` did not exist. The two
pre-existing-boundary assertions passed.

Final focused command:

```text
py -3.11 -m pytest -q tests/test_three_project_boundaries.py tests/test_v5_closeout.py
```

Final exit: 0. Result: `7 passed in 1.10s`.

## Compile and static

```text
py -3.11 -m compileall -q src tests/test_three_project_boundaries.py
py -3.11 -m ruff check src tests/test_three_project_boundaries.py
```

Exit: 0. Ruff result: `All checks passed!`.

YAML safe-load and `git diff --check` both exited 0. The upstream revisions and
license digests were independently re-read from the three read-only checkouts
and match `docs/architecture/THREE_PROJECT_UPSTREAM_SNAPSHOT.yaml`.

## Boundary result

The runtime dependency list is empty. Static checks cover `src/manju`, `skills`,
and `pyproject.toml`. ViMax and Toonflow remain clean-room method references;
OpenChatCut remains an optional process-external local manual editor. Absence of
all three is supported and does not block core Manju workflows.

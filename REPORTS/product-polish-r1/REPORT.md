# Product Polish R1 Report

## Baseline

- HEAD before changes: `fd93f52e8f16f58d24f5041972a6e80eb6d894c3`
- Fixture: local `manju new --demo` project, no cloud provider, no API key, no
  external transport.
- GUI: `manju gui ... --host 127.0.0.1 --port 0 --no-open`.

## Verification

Focused command:

```text
python -m pytest tests/test_product_polish_home_focus.py tests/test_zero_cost_provider_policy.py tests/test_cockpit.py tests/test_gui.py tests/test_gui_core.py tests/test_gui_modes.py tests/test_gui_page_shell_scripts.py -q
```

Result: `57 passed`.

Static checks:

- `python -m compileall -q src/manju`
- `git diff --check`
- served app bundle parsed by the existing GUI Node syntax test.

Browser checks:

- Desktop 1280x720: build panel y=641 px after polish; zero horizontal overflow;
  no console warnings/errors.
- Strict zero-cost desktop: build panel y=603 px; visible `严格零成本` chip with
  loopback/credential-free tooltip; zero horizontal overflow; no console errors.
- Mobile 390x844 baseline: zero horizontal overflow.
- Header help still opens the complete six-step onboarding checklist on demand.

Known repository baseline remains unchanged outside this wave: the full Windows
suite previously reported `6175 passed, 63 skipped, 10 failed`; the 10 failures
were environment-specific missing `grep`/`sh`, unrelated to this change.

# Manju local release-candidate gate

- Profile: `standard`
- Zero-cost: `true`
- Result: `PASS`
- HEAD: `3ad9a3f2c64ae8a7654f4323ae090e1427bc12d6`

| Stage | Required | Status | Duration |
|---|---:|---|---:|
| `compile` | yes | passed | 1.873 |
| `ruff` | no | unavailable |  |
| `product-polish-core-tests` | yes | passed | 20.882 |
| `quick-open-tests` | yes | passed | 6.641 |
| `help-center-tests` | yes | passed | 3.88 |
| `contract-registry-tests` | yes | passed | 2.173 |
| `zero-cost-tests` | yes | passed | 2.123 |
| `roundtrip-baseline-absent-tests` | yes | passed | 2.122 |
| `roundtrip-needs-baseline-tests` | yes | passed | 2.676 |
| `roundtrip-fcpxml-tests` | yes | passed | 3.732 |
| `gui-project-action-tests` | yes | passed | 11.906 |
| `windows-install-tests` | yes | passed | 1.974 |
| `performance-gate` | yes | passed | 29.007 |
| `visual-acceptance` | yes | passed | 7.414 |
| `quick-open-acceptance` | yes | passed | 2.729 |
| `help-center-acceptance` | yes | passed | 2.275 |
| `windows-app-self-test` | yes | passed | 0.669 |
| `wheel-build` | yes | passed | 2.878 |
| `wheel-install-smoke` | yes | passed | 2.902 |

This is a local, zero-cost candidate gate. It does not replace the same-SHA Windows/Ubuntu release gates.

# Local Zero-Cost Proof

This proof validates request semantics and the strict egress boundary without
starting a provider. The fake profile is explicit dogfood data, not an installed
provider and not a fallback for normal projects.

PowerShell:

```powershell
$env:MANJU_EXECUTION_MODE = "strict_zero_cost"
$env:PYTHONPATH = "$PWD/src"
py -3.11 scripts/dogfood/local_proof_preflight.py `
  --profile scripts/dogfood/providers/localhost_fake_video/provider.yaml `
  --output REPORTS/P0-001_LOCAL_PROOF.json
```

The expected document has a compatible preflight, a zero simulated cost, stable
request and execution-profile digests, no credential reference, and
`transport_count: 0`. The script never dials the endpoint. Later fake-provider
tests may start a user-controlled loopback server, but the same policy rejects
any non-loopback URL before a custom or default transport can run.

For CLI dry-run evidence, point `MANJU_PROVIDERS_DIR` at
`scripts/dogfood/providers`,
run `manju providers catalog --json` and `manju providers check
localhost_fake_video --json`, then use a temporary `manju new --demo` project for
`check`, `prompt --check`, and `build --dry-run`. Do not install the profile in
the user provider directory and do not add a credential.

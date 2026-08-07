# Unified Film Authoring Ownership

This document records the AV0 ownership audit for the v5.0 implementation.
It is descriptive only; no runtime code reads it.

The production chain has one direction:

`brief / ending -> scene authoring -> shot production truth -> derived prompt/spec/QC -> media evidence`

`story/creative.yaml` and `story/scenes/*.yaml` are opt-in authoring truth.
`shots/*.yaml` remains the only shot truth and `shots/index.yaml` remains the
membership/order authority. Director proposals stay in
`reports/proposals/*.yaml`; approvals and observations remain append-only in
the existing evidence logs. Coverage, lint, trajectory and context are
rebuildable derived views under `reports/derived/`.

The screen experience layer is intentionally a projection boundary. Screen
intent may inform director views, screen coverage, animatic review and control
views, but it is excluded from prompt compilation, picture SPEC payloads and
expectations. A screen-only edit therefore cannot stale picture media.

SceneContract v1 remains readable forever. New v2 data is additive and never
owns a shot list. Existing projects without the opt-in charter are unchanged.
No external repository is installed as a runtime dependency and no real or
paid Provider is exercised by AV0–AV8.

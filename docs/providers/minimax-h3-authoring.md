# MiniMax H3 Offline Authoring Profile

`minimax_h3` is an unofficial Manju prompt-authoring profile. It is not an
executable Provider and does not imply MiniMax endorsement or certification.

```yaml
id: minimax_h3
kind: authoring_only
network: forbidden
execution: unavailable
cost: not_applicable
requires_api_key: false
```

It reads current shot intent, keyframes, and the existing reference resolver.
It can project `T2VA`, `I2VA`, `L2VA`, `FL2VA`, or `REF2VA`, compile a
deterministic fallback prompt, preserve `generation.prompt_override` verbatim,
and report static warnings/blockers. It cannot submit, poll, download, estimate
cost, qualify a Provider, register a take, or create media.

The recorded 4-15 second and reference-count limits are dated advisory external
metadata. They do not enter routing, qualification, cost, or paid admission.
No H3 output quality was validated in this zero-budget implementation.

```text
manju prompt S001 --target minimax_h3
manju prompt S001 --target minimax_h3 --check
manju prompt S001 --target minimax_h3 --json
manju prompt S001 --target minimax_h3 --bundle
```

Start/end keyframes plus ordinary references are rejected as
`h3_mode_conflict`; the author must choose one H3 input mode explicitly. Missing
music is rendered as `N/A`; Manju does not invent audio, action, emotion,
backstory, dialogue, lyrics, or visible text.

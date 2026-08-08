# Portable Video Authoring Profile

`portable_video` is the provider-neutral offline profile for manually using a
Manju shot with an external video tool.

```yaml
id: portable_video
display_name: Portable Video Handoff
kind: authoring_only
dialect: portable-natural-language-v1
network: forbidden
execution: unavailable
requires_api_key: false
```

Its prompt has fixed sections for shot goal, visual start/change/end, subjects
and references, camera, environment/light, dialogue and visible text, audio,
negative constraints, and the technical target. Missing facts are written as
`Not specified`. Dialogue, lyrics, visible text, and a `prompt_override` are
never translated or rewritten.

The profile does not claim vendor modes or limits. External capability is
`unverified_at_execution`. `UPLOAD_ORDER.md` identifies start/end frames and
physical-reference order; `CONSTRAINTS.md` records the non-invention boundary.

```text
manju prompt S001 --target portable_video
manju prompt S001 --target portable_video --check
manju handoff create S001 --profile portable_video
```

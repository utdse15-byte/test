# MiniMax H3 (offline authoring only)

Use `minimax_h3` only after SceneContract/ShotContract intent and reference
ownership are settled. It is an unofficial prompt projection, not a Provider.

- no keyframes or ordinary refs: `T2VA`
- start keyframe: `I2VA`
- end keyframe: `L2VA`
- start and end keyframes: `FL2VA`
- ordinary refs without endpoints: `REF2VA`
- endpoint keyframe plus ordinary refs: blocker `h3_mode_conflict`

Preserve `generation.prompt_override` byte-for-byte. Without it, compile only
facts already owned by shot intent and resolved references, mark the result
`needs_director_review`, and leave unsupported/missing audio as `N/A`. Never
invent performance, motion, emotion, music, dialogue, lyrics, or visible text.

The reference plan is frozen once. Prompt labels, handoff metadata, refs,
physical assets and checksums consume that same plan. Do not re-select refs in
the exporter or collapse logical Subjects merely because their Picture bytes
match.

H3 limits in the profile are dated advisory metadata. They do not change cost,
routing, qualification, admission, Picture Lock or existing Provider behavior.

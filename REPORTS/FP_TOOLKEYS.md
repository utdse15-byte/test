# FP S4 — Byte-Affecting Toolchain Facts into Render Cache Keys (strictly opt-in)

**Deliverable (user item 7):** the toolchain-manifest loop's DECLARED next step,
landed as a **strictly opt-in** migration: a project that lists tokens in
`project.yaml`'s new `cache_toolchain_keys` folds the named byte-affecting
toolchain facts into every render cache key; a project that never opts in keeps
**byte-identical keys forever** (pinned on literals). The doc's own warning is
enforced at **validation**: only facts that change rendered output bytes are
accepted — anything else (os/python/deps/…) is a structured error, so an
irrelevant fact can never cause a meaningless rebuild.

**Files touched (only these):** `core/models.py` (the field + validator + the
R1 wrap-serializer extended), `core/toolchain.py` (process-cached collector
wrappers + docstring scope update), `media/render.py` (the component helper +
the R2 key sites), `build/graph.py` (the animatic key sites only),
`tests/test_fp_toolkeys.py` (new), this report. `exporters/`,
`media/masters.py`, `core/migrate.py`, `cli.py` **untouched** (live parallel
loops R4/R5).

---

## A. The opt-in surface

```yaml
# project.yaml — absent by default; absent = today's behaviour, forever
cache_toolchain_keys: [ffmpeg, fonts]
```

| Token | Fact folded into the keys | Honest gap value |
|---|---|---|
| `ffmpeg` | the `ffmpeg -version` FIRST line, verbatim (the same fact `manju.toolchain-manifest/v1` records) | `"missing"` |
| `fonts` | the drawtext burn-font content hash (`media/card.find_font` — both burn surfaces' locator), `sha256:…` | `"unknown"` |

Validation — allowed tokens **EXACTLY** `("ffmpeg", "fonts")`
(`core/models.py::CACHE_TOOLCHAIN_TOKENS`, enforced by
`_byte_affecting_toolchain_tokens`):

| Input | Result |
|---|---|
| absent / `None` | valid — the off switch; **dropped from every dump form** (R1 edit_rate drop-None precedent, same wrap serializer) |
| `[ffmpeg]`, `[fonts]`, `[ffmpeg, fonts]`, `[fonts, ffmpeg]` | valid (authored order kept; the key component is order-insensitive) |
| any other token (`os`, `python`, `deps`, `locale`, `manju`, `ffprobe`, …) | **structured error** naming the rejected token, the allowed set, and WHY: those facts do not change output bytes — keying them would only cause 毫无意义的全量重建 (keys move, bytes don't) |
| `[]` | error — 空列表 is not a switch state; delete the field instead |
| duplicate token | error — the component is a sorted token→fact map, repetition adds nothing |
| non-list shapes (`"ffmpeg"`, `7`, mapping) | pydantic type error |
| assignment on a loaded config | same gate (`validate_assignment`) |

**Documented consequence (rebuild honesty):** flipping the field ON — or later
OFF — changes every render cache key of that project, so the next build is
**cache-cold end to end** (segments, boundaries, finals, animatic re-render
once). That IS the feature: a new ffmpeg / swapped burn font honestly
re-renders instead of serving stale-toolchain bytes. Stated on the field's
comment block in `core/models.py` and here. `manju explain` / impact surfaces
need no change — the component sits inside the key payload/sidecar breakdown,
so keys explain themselves.

## B. Key plumbing — R2's four sites, drop-when-absent exactly like `rate_key`

The single gate is `media/render.py::_toolchain_key_component(project,
config=None)` → `None` when the field is absent (NO collector probed at all —
spy-pinned), else the sorted token→fact map over exactly the declared tokens.

| # | Site | Opted-in | Absent (`None`) |
|---|---|---|---|
| 1 | `media/render.py::_segment_cache_key` | `parts.append(("toolchain", map))` — appended AFTER the R2 `("rate", …)` part | appends nothing → pre-S4 literal, pinned |
| 2 | `media/render.py::_boundary_cache_key` | the component rides **both neighbour segment keys** (`ka`/`kb`) — exactly R2's `rate_key` routing; the trailer formula is unchanged | byte-identical, pinned |
| 3 | `media/render.py::_final_key_payload` | the component threads into every ordered segment key AND lands as a direct `payload["toolchain"]` entry (the final composition pass is itself an ffmpeg/burn-font product, and a `missing:<src>` segment marker folds no facts) — conditional-entry style of `overlay_images`/`look` | no `toolchain` key in the payload; key-set pinned to the six pre-S4 entries |
| 4 | `build/graph.py::_render_animatic` | the kenburns per-still `kb_key["toolchain"]` (mirroring R2's `kb_key["rate"]`) AND the outer animatic payload's conditional `toolchain` entry | both append nothing → sidecar key byte-identical, proven by flip-off returning EXACTLY the original key |

Threading: `render_timeline` reuses `payload.get("toolchain")` — the EXACT
component the content key was computed with — for `_build_segment` /
`_assemble_video_pieces` / `_build_boundary_segment`, so built segment paths
can never drift from the seg keys inside the content key (FIX-A discipline),
and the config is read once. The rational interplay is pinned on literals:
`rate_key` alone appends exactly R2's part; `rate_key` + component appends
`("rate", …)` then `("toolchain", …)`.

Evidence surface: `_key_inputs_breakdown` passes the `toolchain` payload entry
through, so an opted-in final's `.key.json` sidecar still re-hashes back to
`final_key` exactly (WP3 e2 invariant, test-pinned).

## C. Process-cached collectors (`core/toolchain.py`, additive)

`cached_tool_version_line(name)` / `cached_drawtext_font_hash()` — thin
`lru_cache` shells over the SAME collectors the manifest records
(`_tool_version_line`, `_font_inventory`), so N key computations cost **one
probe per process**. Spy evidence (`test_collectors_probe_once_per_process_not_per_key`):
3× final keys + 3× proxy keys + 3× component calls + 10× segment keys + 1×
boundary key ⇒ **ffmpeg probe count = 1, fonts probe count = 1**.
`toolchain_manifest()` itself deliberately keeps probing fresh — it is the
record, and must see a live upgrade mid-process. The record-only inertness pin
(`test_grep_pin_never_read_by_build_core_providers_runtime`) **still holds
verbatim**: the single consumer seam is `media/render.py`; `build/graph.py`
reaches the component only through the render helper; the manifest document is
still never read back by anything.

## D. Byte-identity + behaviour pins (tests/test_fp_toolkeys.py — 23, red-first)

* absent ⇒ pre-S4 **literal** segment/boundary/final key parts, incl. the
  rational interplay; explicit `toolchain_key=None` identical; component
  helper returns `None` without probing any collector;
* serialization: absent field in NO dump form (plain / exclude_none / json);
  save_config byte-stable; opt-in → the yaml line appears; opt-out → **bytes
  restored exactly**;
* ffmpeg token: key shifts exactly when the (monkeypatched) version line
  shifts; deterministic per line; fonts collector NEVER probed when only
  ffmpeg is opted (strictly the declared facts);
* fonts token: key shifts on the font hash; ffmpeg never probed;
* both tokens: sorted token→fact map, authored order irrelevant to the key;
* missing tool ⇒ the honest `"missing"` in the key (real collector on an empty
  PATH — deterministic, never an exception); unlocatable font ⇒ `"unknown"`;
* flip ON changes the final key; flip OFF **restores the byte-identical key**;
* sidecar breakdown carries the component and re-hashes to `final_key`;
* animatic (ffmpeg-marked): opt-in mints a NEW kenburns clip file + a NEW
  outer key; opt-out returns EXACTLY the original key and reuses the original
  clip (both graph.py sites proven live).

## E. Targeted verification (no full suite)

| Run | Result |
|---|---|
| `test_fp_toolkeys.py` (new) | **23 passed** |
| `test_fp_ratemig1 + ratemig2 + fp_toolchain + fp_compat + c16_animatic + dr01_run_evidence` | **80 passed** (R2 render-key literals, R1 serialization literals incl. `_PLAIN_LITERAL`, the record-only grep pin, compat fixtures — all untouched) |
| `test_idempotency + explain + impact + why_stale + hashing + hash_versions + presets + fp_supportbundle + transitions_looks` | **101 passed** |
| `test_c16_transitions` | **3 passed** |
| combined-process pollution check (`toolkeys + fp_toolchain + ratemig2` in one process) | **62 passed** — the autouse fixture clears the lru caches around every test, so a monkeypatched fact can never leak |

Registry note: the `CONTRACTS.yaml` rows for `project.yaml` and
`manju.toolchain-manifest/v1` are the orchestrator's to update (suggested lines
in the loop report — the manifest row's "content-key wiring … not done" clause
is now superseded by this opt-in landing).

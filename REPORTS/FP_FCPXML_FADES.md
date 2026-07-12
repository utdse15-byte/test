# FP Loop Y3 — FCPXML REAL audio fades (DTD-sourced)

Upgrades the V1 gain-only fades branch to **native FCPXML fade elements**. The
grammar is no longer guessed: it is quoted verbatim from Apple's archived FCPXML
**v1.7 DTD** (developer.apple.com FCPXML reference, fetched 2026-07-12), so the
V1-recorded blocker ("confidence in the exact fade element shape is not high")
is dissolved.

## The DTD source (verbatim — the containment chain)

    <!ELEMENT adjust-volume (param*)>
    <!ATTLIST adjust-volume amount CDATA "0dB">
    <!ELEMENT param (fadeIn?, fadeOut?, keyframeAnimation?, param*)>
    <!ATTLIST param name CDATA #REQUIRED>
    <!ELEMENT fadeIn EMPTY>
    <!ATTLIST fadeIn type %fadeType; #IMPLIED>
    <!ATTLIST fadeIn duration %time; #REQUIRED>
    (fadeOut identical; %fadeType; = linear | easeIn | easeOut | easeInOut)

Containment: `asset-clip` → `adjust-volume` → `param` → `fadeIn`/`fadeOut`.

## What is emitted

A **WRITTEN** connected audio clip (non-loop, resolvable duration) carrying a
fade emits:

```xml
<asset-clip ref="r3" lane="-2" ... audioRole="music">
  <adjust-volume amount="-6dB">
    <param name="amount">
      <fadeIn type="linear" duration="6/24s" />
      <fadeOut type="linear" duration="12/24s" />
    </param>
  </adjust-volume>
</asset-clip>
```

### Rulings

1. **`type="linear"` is DELIBERATE.** The render's afade default curve is
   triangular/linear, so a linear FCPXML fade is byte-for-byte parity with what
   we actually render. (The DTD also allows `easeIn|easeOut|easeInOut`; we never
   emit those — we would have no rendered curve to match them.)

2. **`param name="amount"` is a CONVENTION, NOT DTD-mandated.** The DTD only
   requires that `param` carry a `name` **attribute** (`#REQUIRED`); it does not
   dictate the string. `"amount"` is the volume parameter name FCP itself uses,
   so it is the faithful choice — but this is recorded honestly (here and in a
   code comment on `_emit_adjust_volume`) and is never claimed as DTD-mandated.

3. **`amount` is always `"{gain:g}dB"`** (`0.0` → `"0dB"`, harmless — that is the
   DTD's own default for `amount`). V1's **drop-when-zero** for `adjust-volume`
   is preserved **only when there are no fades**; with a fade, `adjust-volume` is
   now the **REQUIRED container** (a `fadeIn`/`fadeOut` can only ride a `param`
   under `adjust-volume`), so it opens even at 0 gain. A zero-gain **zero-fade**
   clip still emits nothing — byte-identical to V1.

4. **Durations: ms → frames (`ROUND_HALF_UP`) → exact rational seconds** via the
   existing `_secs` helper (`"{F·den}/{num}s"`). At 24 fps: 250 ms → 6 frames →
   `"6/24s"`, 500 ms → 12 frames → `"12/24s"`. No float, no drift — the same
   rational-native grid the rest of the exporter rides.

5. **Over-long fades CLAMP to the clip length** with an in-band note (never an
   invalid over-long fade). Each fade is clamped independently to the connected
   clip's whole-frame length. Example — a 2000 ms (48-frame) fade on a 1000 ms
   (24-frame) clip:

   ```xml
   <adjust-volume amount="0dB">
     <param name="amount">
       <fadeIn type="linear" duration="24/24s" />
     </param>
   </adjust-volume>
   <!-- MANJU: audio 'bed.mp3' (music) fade_in 2000ms (48 frames) is longer
        than the clip (24 frames) — clamped to the clip length -->
   ```

6. **Loop-materialized passes carry NO fade.** A materialized loop (W1) repeats
   the WHOLE source per pass (`-stream_loop` render parity), so a per-pass fade
   handle would be wrong audio. The passes carry gain only; the un-expressed fade
   is stated in an honest in-band note on the first pass:

   ```
   MANJU: audio 'room.wav' (ambient) fade_in 500ms / fade_out 500ms not
   expressed as a native fade — a materialized loop repeats the whole source
   per pass (render parity: -stream_loop) and carries no fade handles
   ```

7. **The V1 gain-only fade note DISAPPEARS** on WRITTEN clips whose fades are now
   expressed. Loop / None-duration / ducking notes are unchanged.

## conform.py (fcpxml rows only)

`audio_fade_in` and `audio_fade_out` for target `fcpxml` move **approximated →
preserved**, with detail citing the DTD chain, the ms→frame→rational math, the
over-long clamp, the `type="linear"` afade-parity rationale, and the
`param name="amount"` **convention caveat**. No other conform row is touched.
The fcpxml completeness meta-pin still holds (every feature classified exactly
once; the fcpxml classification pin does not constrain the fade category, so the
move is clean).

## Code

* `src/manju/exporters/fcpxml.py`
  * `_fade_frames(clip, dur_f, bus, name, rate)` — ms→frames ROUND_HALF_UP, each
    clamped to `dur_f`; returns `(fi, fo, clamp_notes)`.
  * `_emit_adjust_volume(parent_clip, clip, rate, fi=0, fo=0)` — the gain/fade
    container (drop-when-absent preserved; `param name="amount"` convention
    documented in-code). The loop path calls it with no fade frames (gain only).
  * `_audio_approx_notes(..., fades_expressed=False)` — suppresses the fade note
    on WRITTEN clips (fades now real); keeps it, reworded, for loop passes.
* `src/manju/exporters/conform.py` — the two fcpxml fade rows.

## Tests

`tests/test_fp_fcpxml_fades.py` (NEW, 18 tests): hand-computed golden with both
fades at exact rational durations; DTD containment chain; `type="linear"`; exact
frame math (parametrized); zero-fade byte identity (drop-when-absent) + a
"differ only in the volume container" proof; zero-gain-with-fades `0dB`
container; only-fade-in / only-fade-out; over-long clamp + note; fade-exactly-
clip-length not clamped; **loop passes carry no fade** + honest note;
determinism.

Red-first captured before implementation (14 failed / 4 passed against the
gain-only code).

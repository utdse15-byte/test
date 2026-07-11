"""AI_IDE_20A golden corpus — the minimal pre-15 regression base (contract
sections 3/4/5/8/9).

TEST DATA ONLY. Everything here lives under ``tests/`` and is self-made
synthetic (PIL geometry/gradients/text; ffmpeg lavfi) — no private or
copyrighted material. Runtime code (``src/manju``) never reads any of it; a
self-test asserts that.

Modules:
    make_visual       deterministic synthetic visual calibration set (committed
                      tiny PNGs, sha256-pinned in manifest.json)
    make_bad_media    deterministic ffmpeg bad-media generator (probe-fact
                      validated; media generated into tmp, not committed)
    fake_reviewer     the offline fake ``qc_vision`` reviewer + disagreeing twin
                      that drive the REAL packet/verdict/intake plumbing
    manifest.json     the versioned corpus manifest (ids, hashes/probe facts,
                      §6 expected observations, blocking policy, repair route,
                      annotator + rationale, reviewer stances)
    providers/        the two vision-slot provider manifests (primary + twin),
                      loaded through the real registry via MANJU_PROVIDERS_DIR
"""

from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent


def manifest_path() -> Path:
    return GOLDEN_DIR / "manifest.json"

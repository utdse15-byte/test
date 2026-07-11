"""AI_IDE_17 WP1/WP4/WP5 — identity reference packs, episode outline packages
and portable template packs.

Three portable text-first shapes, one discipline:

- WP1 :func:`build_reference_pack` / :func:`import_reference_pack` — an
  identity reference pack is a TEXT index over append-only media: per item
  role (front/profile/three_quarter/full_body/expression/…), the EXISTING
  refs-transfer vocabulary (``controls`` / ``ignore`` — providers/refs.py
  ``REF_TRANSFER_VOCAB``, where ``ignore`` is the contract's
  ``must_not_transfer``), and per-item provenance {source,
  license_or_consent, content sha256} — the AI_IDE_18 rights shape. Reuse in
  another project is COPY-ON-IMPORT: bytes are copied under content-addressed
  names, the index is rewritten to project-relative paths, and provenance +
  the origin pack digest ride along, so mutating the external source after
  import provably changes nothing (pin).

- WP4 :func:`load_outline_package` / :func:`inspect_outline` /
  :func:`apply_outline` — EpisodeOutlinePackage: a Skill/Director PROPOSES
  the split (source line spans, titles, target durations, cliffhanger/hook
  notes as ADVISORY text the engine carries verbatim and never invents); the
  engine only validates span coverage/order/duplicates, inspect is
  ZERO-WRITE, and apply creates episodes through the EXISTING
  ``core.series.new_episode`` path.

- WP5 :func:`export_template_pack` / :func:`inspect_template_pack` /
  :func:`import_template_pack` — a portable template pack exported from
  EXPLICITLY selected sources only (style/bible subset, timeline rules,
  delivery profiles, reference packs, voice profile refs), zipped with the
  13C bundle safety discipline (path-safe arcnames, duplicate rejection,
  stable order, SHA256SUMS computed from the streamed bytes). Voice profiles
  MUST pass AI_IDE_18's ``template_export_gate`` — missing rights info
  refuses that profile's listing (never silently packs an unlicensed voice).
  Import is copy-on-import + inspect + explicit conflict handling
  (rename/skip/abort — never a silent overwrite). No marketplace, rating or
  social anything.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from typing import Any

from ..core.container import Project
from ..core.hashing import HASH_PREFIX, hash_file, hash_value
from ..core.yamlio import atomic_write_text, dump_yaml, read_yaml, write_yaml

REFPACK_SCHEMA = "manju.identity-ref-pack/v1"
OUTLINE_SCHEMA = "manju.episode-outline-package/v1"
OUTLINE_SCHEMA_PREFIX = "manju.episode-outline-package/"
TEMPLATE_SCHEMA = "manju.template-pack/v1"

_ZIP_UNSAFE = re.compile(r"^([A-Za-z]:|/|\\)|\.\.")


class SeriesPackError(RuntimeError):
    """A pack could not be built/validated/imported. Message carries no secret."""


# ------------------------------------------------------------- shared helpers


def _check_transfer_vocab(item: dict[str, Any]) -> list[str]:
    """controls/ignore against the ONE existing transfer vocabulary
    (providers.refs.REF_TRANSFER_VOCAB); controls∩ignore is a conflict."""
    from ..providers.refs import REF_TRANSFER_VOCAB

    problems: list[str] = []
    controls = [str(c) for c in (item.get("controls") or [])]
    ignore = [str(c) for c in (item.get("ignore") or [])]
    unknown = sorted({c for c in (*controls, *ignore) if c not in REF_TRANSFER_VOCAB})
    if unknown:
        problems.append("未知 transfer 维度: " + ", ".join(unknown))
    overlap = sorted(set(controls) & set(ignore))
    if overlap:
        problems.append("controls 与 ignore(must_not_transfer)冲突: " + ", ".join(overlap))
    return problems


def _provenance_of(item: dict[str, Any]) -> dict[str, Any]:
    prov = item.get("provenance") or {}
    return {
        "source": prov.get("source"),
        "license_or_consent": prov.get("license_or_consent"),
        "note": prov.get("note"),
    }


def _rights_missing(prov: dict[str, Any]) -> bool:
    return not (str(prov.get("source") or "").strip()
                and str(prov.get("license_or_consent") or "").strip())


# ============================================================ WP1 reference pack


def build_reference_pack(project: Project, pack_id: str, subject: str,
                         items: list[dict[str, Any]], *,
                         out_dir: Path | None = None) -> dict[str, Any]:
    """Export an identity reference pack for ``subject`` from EXPLICIT items.

    Each item: ``{path (project-relative media), role, controls?, ignore?,
    provenance {source, license_or_consent, note}?}``. Bytes are copied into
    the pack directory under content-addressed names; the text index
    ``pack.yaml`` records role/controls/ignore/provenance/sha256 per item.
    Items with unknown transfer dimensions or a controls∩ignore conflict are
    REFUSED; items missing rights info are exported but flagged
    ``rights_missing`` (they will refuse template listing downstream)."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", pack_id or ""):
        raise SeriesPackError(f"pack_id {pack_id!r} 不合法(小写 slug)")
    out = Path(out_dir) if out_dir else (project.root / "exports" / "refpacks" / pack_id)
    media_dir = out / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    index_items: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for item in items:
        rel = str(item.get("path") or "")
        problems = _check_transfer_vocab(item)
        try:
            src = project.resolve(rel)
        except Exception:
            src = None
        if src is None or not src.exists() or src.stat().st_size == 0:
            problems.append(f"媒体不存在或为空: {rel}")
        if problems:
            refused.append({"path": rel, "problems": problems})
            continue
        sha = hash_file(src)
        dest_name = sha[len(HASH_PREFIX):][:16] + src.suffix.lower()
        dest = media_dir / dest_name
        if not dest.exists():
            dest.write_bytes(src.read_bytes())
        prov = _provenance_of(item)
        index_items.append({
            "file": f"media/{dest_name}",
            "role": str(item.get("role") or "reference"),
            "controls": [str(c) for c in (item.get("controls") or [])],
            "ignore": [str(c) for c in (item.get("ignore") or [])],
            "sha256": sha,
            "bytes": src.stat().st_size,
            "provenance": prov,
            "rights_missing": _rights_missing(prov),
        })

    index = {
        "schema": REFPACK_SCHEMA,
        "pack_id": pack_id,
        "subject": subject,
        "items": index_items,
        "pack_digest": hash_value({"s": subject, "i": [
            {k: it[k] for k in ("file", "role", "sha256")} for it in index_items]}),
    }
    write_yaml(out / "pack.yaml", index)
    return {"pack_dir": str(out), "index": index, "refused": refused}


def import_reference_pack(project: Project, pack_dir: Path | str
                          ) -> dict[str, Any]:
    """COPY-ON-IMPORT a reference pack into ``project``.

    Bytes are copied to ``media/refs/imported/<sha16><ext>`` (content-addressed
    — a re-import of identical bytes is a no-op), the index is rewritten with
    PROJECT-RELATIVE paths into ``bible/refpacks/<pack_id>.yaml``, and each
    item's sha256 is verified against the pack's recorded hash (a tampered
    pack refuses). Origin provenance + the pack digest ride along, so the
    identity/voice lineage stays traceable across projects (§10). After this
    returns, the external pack may be mutated or deleted freely — the project
    depends on NOTHING outside its root (pin)."""
    pack_dir = Path(pack_dir)
    index_path = pack_dir / "pack.yaml"
    if not index_path.exists():
        raise SeriesPackError(f"不是 reference pack(缺 pack.yaml): {pack_dir}")
    index = read_yaml(index_path) or {}
    if index.get("schema") != REFPACK_SCHEMA:
        raise SeriesPackError(f"未知 pack schema: {index.get('schema')!r}")
    pack_id = str(index.get("pack_id") or "pack")

    dest_media = project.root / "media" / "refs" / "imported"
    dest_media.mkdir(parents=True, exist_ok=True)
    imported: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    for item in index.get("items") or []:
        rel = str(item.get("file") or "")
        if _ZIP_UNSAFE.search(rel):
            problems.append({"file": rel, "problem": "不安全路径"})
            continue
        src = pack_dir / rel
        if not src.exists():
            problems.append({"file": rel, "problem": "pack 内媒体缺失"})
            continue
        actual = hash_file(src)
        recorded = str(item.get("sha256") or "")
        if recorded and actual != recorded:
            problems.append({"file": rel, "problem":
                             f"hash 不符(pack 被改动?){recorded[:20]}… ≠ {actual[:20]}…"})
            continue
        dest_name = actual[len(HASH_PREFIX):][:16] + src.suffix.lower()
        dest = dest_media / dest_name
        if not dest.exists():
            dest.write_bytes(src.read_bytes())  # append-only copy, content-addressed
        new_item = dict(item)
        new_item["file"] = f"media/refs/imported/{dest_name}"
        new_item["imported_from"] = {
            "pack_id": pack_id,
            "pack_digest": index.get("pack_digest"),
            "original_file": rel,
        }
        imported.append(new_item)

    local_index = {
        "schema": REFPACK_SCHEMA,
        "pack_id": pack_id,
        "subject": index.get("subject"),
        "imported": True,
        "origin_pack_digest": index.get("pack_digest"),
        "items": imported,
    }
    dest_index = project.root / "bible" / "refpacks" / f"{pack_id}.yaml"
    dest_index.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(dest_index, local_index)
    return {"index_path": project.relpath(dest_index),
            "imported": len(imported), "problems": problems,
            "index": local_index}


# ========================================================== WP4 outline package


def load_outline_package(path: Path | str) -> dict[str, Any]:
    """Load + structurally validate an EpisodeOutlinePackage (YAML/JSON).
    Reuses the DR03A safety primitives: secret scan + unsafe-path rejection."""
    from .shotpackage import _reject_unsafe_paths, _scan_secrets

    path = Path(path)
    if not path.exists():
        raise SeriesPackError(f"outline package 不存在: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        _scan_secrets(text)
    except Exception as exc:
        raise SeriesPackError(str(exc)) from exc
    data = read_yaml(path) or {}
    if not isinstance(data, dict):
        raise SeriesPackError("outline package 必须是映射(YAML dict)")
    schema = str(data.get("schema") or "")
    if not schema.startswith(OUTLINE_SCHEMA_PREFIX) or not schema.endswith("v1"):
        raise SeriesPackError(
            f"未知 outline schema {schema!r} — 期望 {OUTLINE_SCHEMA}")
    try:
        _reject_unsafe_paths(data)
    except Exception as exc:
        raise SeriesPackError(str(exc)) from exc
    # `source_script` is path-like but its KEY does not contain "path", so the
    # DR03A recursive scan above would not see it — check it explicitly.
    from .shotpackage import _is_unsafe_path

    src = data.get("source_script")
    if src is not None and _is_unsafe_path(str(src)):
        raise SeriesPackError(
            f"source_script {src!r} 不合法 — 必须是 series 内相对路径(无 '..'、不以 / 开头)")
    if not isinstance(data.get("episodes"), list) or not data["episodes"]:
        raise SeriesPackError("outline package 没有 episodes 列表")
    return data


def _outline_diagnostics(package: dict[str, Any],
                         source_lines: int | None) -> list[dict[str, Any]]:
    """The engine's WHOLE creative jurisdiction (contract §8): source span
    coverage / order / duplicates / id shape. Titles, target durations,
    cliffhangers and hooks are ADVISORY author fields carried verbatim —
    validating their content would be inventing beats."""
    from ..core.series import _EID_RE

    diags: list[dict[str, Any]] = []
    seen: set[str] = set()
    prev_end = 0
    for ep in package.get("episodes") or []:
        eid = str(ep.get("id") or "")
        if not _EID_RE.match(eid):
            diags.append({"code": "EPISODE_ID_INVALID", "episode": eid})
            continue
        if eid in seen:
            diags.append({"code": "EPISODE_ID_DUPLICATE", "episode": eid})
            continue
        seen.add(eid)
        span = ep.get("source_span") or {}
        start = span.get("start_line")
        end = span.get("end_line")
        if not isinstance(start, int) or not isinstance(end, int):
            diags.append({"code": "SPAN_MISSING", "episode": eid,
                          "detail": "source_span.start_line/end_line 必须是整数"})
            continue
        if start < 1 or end < start:
            diags.append({"code": "SPAN_INVALID", "episode": eid,
                          "detail": f"{start}-{end}"})
            continue
        if start <= prev_end:
            diags.append({"code": "SPAN_OVERLAP_OR_DISORDER", "episode": eid,
                          "detail": f"{start} ≤ 上一集结束行 {prev_end}(必须有序不重叠)"})
        if source_lines is not None and end > source_lines:
            diags.append({"code": "SPAN_OUT_OF_RANGE", "episode": eid,
                          "detail": f"end_line {end} > 源文件 {source_lines} 行"})
        prev_end = max(prev_end, end)
    return diags


def inspect_outline(series: Any, package: dict[str, Any]) -> dict[str, Any]:
    """ZERO-WRITE inspection (pin): what apply WOULD do — per episode its id,
    title, target duration, advisory cliffhanger/hook text (verbatim — the
    engine never fills these in), its span slice size and whether the episode
    already exists. Touches nothing on disk."""
    src_rel = package.get("source_script")
    source_text = None
    source_lines = None
    if src_rel:
        p = series.root / str(src_rel)
        if p.exists():
            source_text = p.read_text(encoding="utf-8")
            source_lines = len(source_text.splitlines())
    diags = _outline_diagnostics(package, source_lines)
    rows: list[dict[str, Any]] = []
    from ..core.container import PROJECT_FILE

    for ep in package.get("episodes") or []:
        eid = str(ep.get("id") or "")
        span = ep.get("source_span") or {}
        exists = (series.episode_project_dir(eid) / PROJECT_FILE).exists() if eid else False
        rows.append({
            "id": eid,
            "title": str(ep.get("title") or ""),
            "target_duration_s": ep.get("target_duration_s"),
            "cliffhanger": ep.get("cliffhanger"),   # ADVISORY, verbatim, may be None
            "hook": ep.get("hook"),                 # ADVISORY, verbatim, may be None
            "span": [span.get("start_line"), span.get("end_line")],
            "characters": list(ep.get("characters") or []),
            "locations": list(ep.get("locations") or []),
            "exists": exists,
            "would_create": not exists,
        })
    return {
        "schema": "manju.episode-outline-inspect/v1",
        "source_script": src_rel,
        "source_lines": source_lines,
        "episodes": rows,
        "diagnostics": diags,
        "ok": not diags,
        "note": "零写入检查:cliffhanger/hook/时长为作者建议字段,引擎原样携带、"
                "从不自行发明;apply 走既有 new_episode 路径。",
    }


def apply_outline(series: Any, package: dict[str, Any], *,
                  actor: str = "human") -> dict[str, Any]:
    """Create the proposed episodes through the EXISTING ``new_episode`` path
    and write each episode's script slice (its source span). Refuses up front
    on any diagnostic — a package that fails inspection can not be applied.
    An episode's already-edited script.md is left untouched (reported, not
    overwritten — same honesty as split_script round-W #75)."""
    from ..core.series import _SCRIPT_SCAFFOLD, new_episode
    from ..core.yamlio import atomic_write_text as _write

    inspect = inspect_outline(series, package)
    if not inspect["ok"]:
        raise SeriesPackError(
            "outline 校验未通过,拒绝 apply: "
            + "; ".join(f"{d['code']}({d.get('episode')})" for d in inspect["diagnostics"][:6]))
    src_rel = package.get("source_script")
    source_text = None
    if src_rel:
        p = series.root / str(src_rel)
        source_text = p.read_text(encoding="utf-8") if p.exists() else None
    lines = source_text.splitlines() if source_text else None

    results: list[dict[str, Any]] = []
    for row in inspect["episodes"]:
        eid = row["id"]
        item = {"id": eid, "created": False, "script_written": False, "skipped": None}
        if row["would_create"]:
            project = new_episode(series, eid, title=row["title"], actor=actor)
            item["created"] = True
        else:
            project = series.open_episode(eid)
        if lines is not None and row["span"][0] and row["span"][1]:
            body = "\n".join(lines[row["span"][0] - 1: row["span"][1]]).strip()
            content = f"# {row['title'] or eid}\n\n" + (body + "\n" if body else "")
            dest = project.root / "story" / "script.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            on_disk = dest.read_text(encoding="utf-8") if dest.exists() else None
            if on_disk is not None and on_disk not in (content, _SCRIPT_SCAFFOLD):
                item["skipped"] = "story/script.md 已被编辑,未覆盖(同 split-script 纪律)"
            else:
                _write(dest, content)
                item["script_written"] = True
        results.append(item)
    return {"applied": results, "package_digest": hash_value(package)}


# ============================================================ WP5 template pack


def _pack_arcname(name: str) -> str:
    if _ZIP_UNSAFE.search(name) or ".." in Path(name).parts:
        raise SeriesPackError(f"不安全的 pack 成员名: {name!r}")
    return name


def export_template_pack(project: Project, out_zip: Path | str, *,
                         bible_entries: list[str] | None = None,
                         include_style: bool = False,
                         include_rules: bool = False,
                         include_delivery_profiles: bool = False,
                         voice_profiles: list[str] | None = None,
                         media_files: list[str] | None = None,
                         license_note: str = "") -> dict[str, Any]:
    """Export a portable template pack from EXPLICITLY selected sources only —
    nothing is implied, no hidden vendor defaults ride along (contract §9).

    VOICE-RIGHTS GATE (ruling 6, the AI_IDE_18 surface): every requested voice
    profile goes through ``build.voiceid.template_export_gate``; a profile with
    missing rights info is REFUSED (listed under ``refused_voice_profiles``
    with the gate's reasons, and NOT packed). The pack is a ZIP with the 13C
    bundle discipline: path-safe arcnames, duplicate rejection, stable member
    order, SHA256SUMS computed from the exact streamed bytes."""
    from ..core.check import SECRET_PATTERNS
    from .voiceid import character_profile, template_export_gate

    out_zip = Path(out_zip)
    bible = project.load_bible()

    members: list[tuple[str, bytes]] = []
    seen: set[str] = set()

    def _add(arcname: str, data: bytes) -> None:
        arcname = _pack_arcname(arcname)
        if arcname in seen:
            raise SeriesPackError(f"重复的 pack 成员: {arcname}")
        seen.add(arcname)
        members.append((arcname, data))

    selections: dict[str, Any] = {}
    refused_voice: list[dict[str, Any]] = []

    # bible subset — only the ids explicitly named.
    subset: dict[str, dict[str, Any]] = {}
    for aid in bible_entries or []:
        if aid not in bible:
            raise SeriesPackError(f"bible 里没有 {aid!r} — 模板只从明确选择的源导出")
        subset[aid] = bible[aid]
    if subset:
        _add("bible/entries.yaml", dump_yaml(subset).encode("utf-8"))
        selections["bible_entries"] = sorted(subset)

    if include_style:
        style_path = project.root / "bible" / "style.yaml"
        if style_path.exists():
            _add("bible/style.yaml", style_path.read_bytes())
            selections["style"] = True

    if include_rules:
        rules_path = project.rules_path
        if rules_path.exists():
            _add("timeline/rules.yaml", rules_path.read_bytes())
            selections["rules"] = True

    if include_delivery_profiles:
        config = project.load_config()
        raw = getattr(config, "model_extra", None) or {}
        profiles = raw.get("delivery_profiles")
        if isinstance(profiles, dict) and profiles:
            _add("delivery/profiles.yaml", dump_yaml(profiles).encode("utf-8"))
            selections["delivery_profiles"] = sorted(profiles)

    # voice profiles — EVERY one through the 18 gate; refusal is per-profile.
    packed_voice: dict[str, Any] = {}
    for cid in voice_profiles or []:
        profile = character_profile(project, cid)
        gate = template_export_gate(profile)
        if gate["blocked"]:
            refused_voice.append({"character": cid, "reasons": gate["reasons"]})
            continue
        packed_voice[cid] = {"profile": profile, "gate": gate}
    if packed_voice:
        _add("voice/profiles.yaml", dump_yaml(packed_voice).encode("utf-8"))
        selections["voice_profiles"] = sorted(packed_voice)

    # explicit media (key art / reference media) — content-addressed arcnames.
    media_index: list[dict[str, Any]] = []
    for rel in media_files or []:
        src = project.resolve(rel)
        if not src.exists():
            raise SeriesPackError(f"媒体不存在: {rel}")
        sha = hash_file(src)
        arc = f"media/{sha[len(HASH_PREFIX):][:16]}{src.suffix.lower()}"
        if arc not in seen:
            _add(arc, src.read_bytes())
        media_index.append({"file": arc, "sha256": sha, "original": rel})
    if media_index:
        selections["media"] = [m["original"] for m in media_index]

    manifest = {
        "schema": TEMPLATE_SCHEMA,
        "selections": selections,
        "media_index": media_index,
        "license": {"note": license_note},
        "refused_voice_profiles": refused_voice,
        "provenance": {"exported_from": project.root.name},
    }
    manifest["pack_digest"] = hash_value(
        {"sel": selections, "media": media_index,
         "members": sorted(seen)})
    manifest_text = dump_yaml(manifest)
    for pattern in SECRET_PATTERNS:  # the ONE token list — a pack never carries a key
        if pattern.search(manifest_text):
            raise SeriesPackError("模板包含类 API key 的 token — 拒绝导出")
    _add("manifest.yaml", manifest_text.encode("utf-8"))

    members.sort(key=lambda t: t[0])  # stable order
    sums: list[str] = []
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_zip.with_suffix(out_zip.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in members:
            digest = hashlib.sha256(data).hexdigest()  # the exact streamed bytes
            sums.append(f"{digest}  {arcname}")
            zf.writestr(zipfile.ZipInfo(arcname), data)
        zf.writestr(zipfile.ZipInfo("SHA256SUMS"),
                    ("\n".join(sums) + "\n").encode("utf-8"))
    tmp.replace(out_zip)
    return {"zip": str(out_zip), "members": [m[0] for m in members],
            "manifest": manifest, "refused_voice_profiles": refused_voice}


def inspect_template_pack(zip_path: Path | str) -> dict[str, Any]:
    """Read-only pack inspection: manifest + member list + SHA256SUMS
    verification against the actual member bytes. Writes nothing."""
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise SeriesPackError(f"pack 不存在: {zip_path}")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for n in names:
            if _ZIP_UNSAFE.search(n):
                raise SeriesPackError(f"pack 含不安全成员名: {n!r}")
        if "manifest.yaml" not in names or "SHA256SUMS" not in names:
            raise SeriesPackError("不是 template pack(缺 manifest.yaml/SHA256SUMS)")
        import yaml as _yaml

        manifest = _yaml.safe_load(zf.read("manifest.yaml").decode("utf-8")) or {}
        if manifest.get("schema") != TEMPLATE_SCHEMA:
            raise SeriesPackError(f"未知 template schema: {manifest.get('schema')!r}")
        mismatches: list[str] = []
        recorded: dict[str, str] = {}
        for line in zf.read("SHA256SUMS").decode("utf-8").splitlines():
            if "  " in line:
                digest, name = line.split("  ", 1)
                recorded[name] = digest
        for name in names:
            if name == "SHA256SUMS":
                continue
            actual = hashlib.sha256(zf.read(name)).hexdigest()
            if recorded.get(name) != actual:
                mismatches.append(name)
    return {"manifest": manifest, "members": sorted(names),
            "checksum_mismatches": mismatches, "ok": not mismatches}


def import_template_pack(project: Project, zip_path: Path | str, *,
                         on_conflict: str = "abort") -> dict[str, Any]:
    """COPY-ON-IMPORT a template pack into ``project``.

    - checksums verified first (a tampered pack refuses);
    - bible entries merge into the project bible; an id that already exists is
      a CONFLICT handled per ``on_conflict``: ``abort`` (default — nothing
      written), ``skip`` (keep local), or ``rename`` (imported id becomes
      ``<id>_imported``); NEVER a silent overwrite (ruling 6);
    - media land content-addressed under ``media/refs/imported/`` (append-only);
    - the imported provenance (pack digest, origin) is recorded to
      ``bible/refpacks/template_import_<digest12>.yaml`` so identity/voice
      lineage stays traceable (§10);
    - after import the external zip may change freely — the project holds its
      own copies (copy-on-import pin)."""
    if on_conflict not in ("abort", "skip", "rename"):
        raise SeriesPackError(f"on_conflict 只能是 abort/skip/rename(得到 {on_conflict!r})")
    info = inspect_template_pack(zip_path)
    if not info["ok"]:
        raise SeriesPackError(
            "pack 校验失败(SHA256SUMS 不符): " + ", ".join(info["checksum_mismatches"]))
    manifest = info["manifest"]
    digest12 = str(manifest.get("pack_digest") or "")[len(HASH_PREFIX):][:12] or "unknown"

    import yaml as _yaml

    conflicts: list[dict[str, Any]] = []
    imported: dict[str, Any] = {"bible_entries": [], "media": [], "voice_profiles": []}

    with zipfile.ZipFile(Path(zip_path)) as zf:
        names = set(zf.namelist())

        # ---- bible subset with explicit conflict policy (dry-run first) ----
        incoming: dict[str, Any] = {}
        if "bible/entries.yaml" in names:
            incoming = _yaml.safe_load(zf.read("bible/entries.yaml").decode("utf-8")) or {}
        local_bible = project.load_bible()
        plan: list[tuple[str, str, Any]] = []  # (final_id, action, entry)
        for aid, entry in incoming.items():
            if aid in local_bible:
                if on_conflict == "abort":
                    raise SeriesPackError(
                        f"bible 冲突: {aid!r} 已存在 — on_conflict=abort,未写入任何内容"
                        "(改用 skip 保留本地或 rename 重命名导入)")
                if on_conflict == "skip":
                    conflicts.append({"id": aid, "action": "skipped_local_kept"})
                    continue
                new_id = f"{aid}_imported"
                conflicts.append({"id": aid, "action": "renamed", "to": new_id})
                plan.append((new_id, "rename", entry))
            else:
                plan.append((aid, "add", entry))

        # ---- writes (only after the whole plan validated) ----
        chars_path = project.root / "bible" / "characters.yaml"
        chars = read_yaml(chars_path) or {}
        for final_id, _action, entry in plan:
            entry = dict(entry) if isinstance(entry, dict) else {"value": entry}
            entry["imported_from"] = {"pack_digest": manifest.get("pack_digest"),
                                      "origin": (manifest.get("provenance") or {}).get(
                                          "exported_from")}
            chars[final_id] = entry
            imported["bible_entries"].append(final_id)
        if plan:
            atomic_write_text(chars_path, dump_yaml(chars))

        # ---- media: content-addressed append-only copies ----
        dest_media = project.root / "media" / "refs" / "imported"
        for m in manifest.get("media_index") or []:
            arc = str(m.get("file") or "")
            if arc not in names:
                continue
            data = zf.read(arc)
            sha = "sha256:" + hashlib.sha256(data).hexdigest()
            dest_media.mkdir(parents=True, exist_ok=True)
            dest = dest_media / (sha[len(HASH_PREFIX):][:16] + Path(arc).suffix)
            if not dest.exists():
                dest.write_bytes(data)
            imported["media"].append(project.relpath(dest))

        # ---- voice profiles ride as provenance-bearing records (no bible
        # mutation beyond the entries above; the profiles file is evidence) ----
        voice_payload = None
        if "voice/profiles.yaml" in names:
            voice_payload = _yaml.safe_load(zf.read("voice/profiles.yaml").decode("utf-8"))
            imported["voice_profiles"] = sorted(voice_payload or {})

    record = {
        "schema": "manju.template-import/v1",
        "pack_digest": manifest.get("pack_digest"),
        "origin": manifest.get("provenance"),
        "license": manifest.get("license"),
        "imported": imported,
        "conflicts": conflicts,
        "voice_profiles": voice_payload,
    }
    rec_path = project.root / "bible" / "refpacks" / f"template_import_{digest12}.yaml"
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(rec_path, record)
    return {"record": project.relpath(rec_path), "imported": imported,
            "conflicts": conflicts}

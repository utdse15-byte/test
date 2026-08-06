"""Task-oriented workflow navigation (FP loop J, roadmap §8.3 — navigation only).

我想做 X,该按什么顺序敲哪些命令?This module is the ONE curated answer: a
cli-side WORKFLOWS table mapping real production tasks (first build, QC→repair,
deliver, recover, …) onto the commands that already exist, in the order a
person would actually run them.

Scope ruling (binding for this loop):

- NAVIGATION ONLY. The table changes no behaviour anywhere: no aliases, no
  deprecations, no shell completion, no new engine facts.
- The table is CODE — a cli-side dict rendered by ``manju help-workflow`` —
  NOT a new fact source and NOT a contract document. Its ``--json`` output is
  plain CLI JSON, covered by the existing cli-json-surface document row in
  CONTRACTS.yaml; deliberately NO ``manju.*/vN`` schema id is registered.
- Honesty is test-enforced: every command string a step (or see_also row)
  names must resolve in the LIVE typer registry — tests/test_fp_workflows.py
  reuses the same resolver mechanism that keeps the README command table
  honest (tests/test_fp_docs.py). An aspirational command fails RED.

Entry shape: ``{title, when, steps: [(command, why), …], next, see_also}``
where ``next`` is another WORKFLOWS key (or None) and every command string
starts with ``manju ``. Placeholders use ``<…>`` so the resolver strips them.
"""

from __future__ import annotations

from typing import Iterator

WORKFLOWS: dict[str, dict] = {
    "new-project": {
        "title": "从零到获批 Animatic / new project → approved animatic",
        "when": "你有想法或素材,但还没有 .manju 项目",
        "steps": [
            ("manju new <名字> --preset vertical_ai_video",
             "scaffold the .manju project (auto git init); a preset kit pre-fills "
             "frame + technical defaults only — `manju presets` lists the three "
             "kits。然后 cd <名字>.manju 进入项目 — 后面的每条命令都在项目里运行"
             "(round-2 audit: the one unwritten step every cold start tripped on)"),
            ("manju create",
             "show the staged authoring checklist through Storyboard; production "
             "continues through the Animatic and proof gates below"),
            ("manju import <files...>",
             "register your own footage/audio into read-only media/imports/ — "
             "the engine never touches originals"),
            ("manju check",
             "schema + references + locks + secret scan; a failing check blocks build"),
            ("manju build --target animatic",
             "compile the full shot order, temporary dialogue/sound, durations and "
             "keyframes into a spend-free continuous animatic"),
            ("manju production status",
             "verify that scene/shot contracts and the current animatic pass before "
             "asking for content approval"),
            ("manju production approve-animatic <animatic> --reason <why>",
             "human-only approval of the current animatic's exact path, bytes and "
             "content key; --yes cannot replace it"),
        ],
        "next": "refine-shot",
        "see_also": ["manju presets", "manju status", "manju doctor"],
    },
    "refine-shot": {
        "title": "写/改一个镜头(提案→确认)/ write or refine a shot",
        "when": "要新增或修改 shots/*.yaml,想先看影响与成本再动真相文本",
        "steps": [
            ("manju status",
             "takeover entry point: current phase, gaps and the suggested next step"),
            ("manju mentions --check",
             "resolve @角色/@场景 mentions in shot free text against the bible "
             "before they drift out of sync"),
            ("manju impact <shot>",
             "read-only: what this edit would stale, recompile and cost — "
             "check before touching truth"),
            ("manju director propose -f <plan.yaml>",
             "step 1 of the AI-director contract: validate + annotate impact/cost, "
             "persist the plan — nothing runs, nothing is spent"),
            ("manju director confirm <proposal_id>",
             "step 3: the explicit approve-before-execute gate — paid steps ride "
             "this confirmation"),
            ("manju director run <proposal_id>",
             "steps 4-6: auto-snapshot first, execute in order, stop at the first "
             "failure, then show the diff and next-step suggestions"),
            ("manju check",
             "re-validate the changed truth text; broken truth cannot enter a build"),
        ],
        "next": "generate-takes",
        "see_also": ["manju prompt <shot> --check", "manju propose <标题> --body <正文>",
                     "manju lock <shot> <field>"],
    },
    "generate-takes": {
        "title": "生成候选 take 并选用 / generate takes + select",
        "when": "Animatic 已批准,按 proof shot → proof scene → bulk 的范围逐步生成",
        "steps": [
            ("manju production status",
             "read the current AUTHORING / PROOF_SHOT_READY / PROOF_SCENE_READY / "
             "BULK_READY stage and the paid shot scope it permits"),
            ("manju build --dry-run",
             "see the exact stage-limited generation plan, readiness gates and cost "
             "before any provider transport"),
            ("manju build",
             "generate only the current proof scope first; review proof shots before "
             "proof-scene shots, and do not start bulk while status is blocked"),
            ("manju redo <shot> --candidates <N>",
             "force fresh takes for one shot (append-only: the current selection "
             "stands until you pick)"),
            ("manju tasks",
             "the run-ledger view: job statuses, failures/rejections, spend by provider"),
            ("manju board --serve",
             "click through takes in the browser and 选用/重做 — the same engine "
             "calls as the CLI, every click recorded as an event"),
            ("manju select <shot> <take>",
             "pick the winner; the decision is one reviewable, revertible line of YAML"),
            ("manju production approve-proof-scene <scene> --reason <why>",
             "after continuous human viewing, bind approval to the ordered selected "
             "takes, media hashes, trims, contracts, expectations and audio timing"),
            ("manju production status",
             "repeat the proof loop until BULK_READY; only then may the next build "
             "perform full paid generation"),
        ],
        "next": "qc-repair",
        "see_also": ["manju routing explain <shot>", "manju compare", "manju spend"],
    },
    "qc-repair": {
        "title": "质检 → 修复 → 复验 / QC → repair → re-verify",
        "when": "有渲染产物了,想知道哪里不对并把它修掉",
        "steps": [
            ("manju qc",
             "three-layer QC → reports/qc.json + qc.md + repair_plan.yaml; "
             "exits non-zero while errors remain"),
            ("manju repair --auto",
             "apply only the auto-safe part of the repair plan — every fix is an "
             "append-only new take, originals untouched"),
            ("manju repair --op <op> --shot <shot>",
             "targeted clip op (retime/extend/trim/croppad/inout/voice) when "
             "auto-safe is not enough"),
            ("manju build --target final",
             "re-render with the repairs folded in — unchanged work skips via the "
             "content key"),
            ("manju qc",
             "re-verify: the gate must go green before you deliver"),
        ],
        "next": "preview-final",
        "see_also": ["manju qc brief", "manju qc verdict --from-file <findings.json>",
                     "manju failures"],
    },
    "preview-final": {
        "title": "预演阶梯 → 正式成片 / preview ladder → final",
        "when": "想在花大钱生成/渲染之前,先便宜地听到、看到片子的样子",
        "steps": [
            ("manju build --target audition",
             "先听后看: voice + captions + music on slate video — no picture "
             "generation, missing takes allowed"),
            ("manju build --target animatic",
             "deterministic keyframe + ken-burns pre-vis over the existing "
             "audio/captions — still no video generation spend"),
            ("manju production approve-animatic <animatic> --reason <why>",
             "human approval of the exact current animatic before paid proof work"),
            ("manju production status",
             "advance through proof shots and proof scenes; full generation waits for "
             "BULK_READY"),
            ("manju build --target proxy",
             "cheap assembly pass; generation inside the plan still obeys the current "
             "production-readiness scope"),
            ("manju explain --cost",
             "read-only: why the next build will do what it will do, with "
             "est_cost totals"),
            ("manju build --target final",
             "the real render; content-key idempotent — --force re-renders regardless"),
            ("manju package",
             "cut cover.png (+ teaser.mp4) out of the freshly built final"),
        ],
        "next": "deliver",
        "see_also": ["manju build --mode <mode>", "manju frames <source>", "manju spend"],
    },
    "captions-localization": {
        "title": "字幕与多语言 / captions + localization",
        "when": "需要字幕(真人素材或 TTS)或要再出一个语种的版本",
        "steps": [
            ("manju transcribe <media> --from-srt <srt>",
             "real-footage subtitles: normalize your own SRT (or --text auto-times "
             "a transcript) — works today with no ASR vendor"),
            ("manju align <shot> --from-srt <srt>",
             "align imported human VO to the script → <take>.timing.json, which "
             "drives word-timed captions"),
            ("manju voice <shot> --preview",
             "试听 a disposable TTS sample first — never becomes a take"),
            ("manju voice --missing",
             "batch-synthesize every missing dialogue line (append-only; newest wins)"),
            ("manju locale add <lang>",
             "scaffold the locale overlay (lines.yaml + base_hash); the picture "
             "pipeline stays shared"),
            ("manju locale status <lang>",
             "which lines are translated vs stale against the base text"),
            ("manju build --lang <lang>",
             "voice + captions + final under locales/<lang> — video segments are "
             "never regenerated"),
        ],
        "next": "preview-final",
        "see_also": ["manju export --srt", "manju qc"],
    },
    "deliver": {
        "title": "交付(清单→平台交接→符合性)/ deliver",
        "when": "成片满意了,要打包交给平台、剪辑软件或别人",
        "steps": [
            ("manju exports",
             "导出中心: every deliverable's freshness at a glance; --json adds the "
             "release_assessment (blockers / ready / next safe actions)"),
            ("manju export --jianying --srt --otio",
             "produce the NLE drafts + captions + OTIO interchange from the "
             "compiled timeline"),
            ("manju exports --profile <id> --manifest",
             "derive the delivery manifest (variant/NLE/localization/platform-"
             "handoff facts) — a derived report, never a build input"),
            ("manju exports --profile <id> --bundle",
             "pack exactly the manifest's files + SHA256SUMS into a "
             "byte-deterministic zip (distinct from `manju pack`)"),
            ("manju qc conformance",
             "PASS/FAIL/UNKNOWN per technical target from the profile — UNKNOWN "
             "stays honest, and there is deliberately no aggregate score"),
            ("manju exports --approve-baseline --reason <why>",
             "human-only, append-only: bind this final's exact bytes as the "
             "release baseline for later regression review"),
        ],
        "next": None,
        "see_also": ["manju compare --against-baseline", "manju relink report",
                     "manju masters", "manju pull-sheet"],
    },
    "series-episode": {
        "title": "剧集续集 / series episode continuation",
        "when": "在系列伞下开下一集,并保持世界观与角色一致",
        "steps": [
            ("manju series status",
             "cross-episode rollup: shots / finals / spend per episode plus totals "
             "(--health adds season health)"),
            ("manju series new-episode <eid>",
             "scaffold episodes/<eid>.manju as a NORMAL project, its bible seeded "
             "from the series bible"),
            ("manju series sync-bible",
             "conservative sync: missing entries may be added, divergent ones are "
             "reported only (--force kind:id to overwrite)"),
            ("manju series continuity",
             "read-only continuity board: per-episode 完整/缺素材/有问题 verdicts + "
             "the cross-episode character/scene/prop matrix"),
            ("manju series characters",
             "per-character presence and divergence across episodes"),
        ],
        "next": "refine-shot",
        "see_also": ["manju series outline", "manju series split-script",
                     "manju series episodes"],
    },
    "recover": {
        "title": "中断后恢复 / recover after an interruption",
        "when": "进程被杀/断网/关机后回来,不确定哪些活儿(尤其花了钱的)悬着",
        "steps": [
            ("manju status",
             "the takeover entry point: where the project stands, gaps, spend, "
             "next step"),
            ("manju tasks",
             "the run ledger: failures/rejections, and via --json the unresolved "
             "paid submissions with their recovery actions"),
            ("manju tasks attach-remote-job <submission_id> <remote_job_id>",
             "once YOU confirmed the remote job really ran: attach it as admitted "
             "evidence — the next build polls it, never resubmits"),
            ("manju tasks abandon <submission_id> --reason <why>",
             "close an unresolvable submission, EXPLICITLY accepting duplicate "
             "risk on the evidence chain"),
            ("manju build",
             "resume: in-flight work is re-polled, cached work skips — a restart "
             "never resubmits paid jobs"),
            ("manju rebuild-index",
             "if .manju/ runtime is gone or corrupt: reconstruct it from "
             "text + media (SQLite is disposable by design)"),
            ("manju unlock <shot> <field>",
             "if your own stale lock blocks the build: unlock is "
             "interactive-terminal-only by design (never over MCP)"),
        ],
        "next": "diagnose",
        "see_also": ["manju failures", "manju history", "manju tasks retry <run_id>"],
    },
    "diagnose": {
        "title": "排障 / diagnose",
        "when": "有东西不对,但还不知道是环境、真相文本还是某次运行的锅",
        "steps": [
            ("manju check",
             "is the truth text itself valid? schema + references + locks + "
             "secret scan"),
            ("manju doctor",
             "environment probes: ffmpeg / fonts / disk / project integrity + "
             "provider manifests + toolbelt"),
            ("manju failures",
             "recent structured failures, newest first: step, cause, evidence, "
             "hint, log path"),
            ("manju events",
             "the collaboration log: who (human/ai/engine) did what, when"),
            ("manju toolchain --diff <old.json>",
             "record-only toolchain facts; --diff prints changed-fact rows — "
             "drift is evidence, never an error"),
            ("manju support-bundle",
             "REDACTED diagnostic zip: default-deny collectors, and a secret-"
             "marker self-scan that refuses to write a leaking bundle"),
        ],
        "next": None,
        "see_also": ["manju explain", "manju watch", "manju history"],
    },
}


def iter_commands() -> Iterator[tuple[str, str]]:
    """Yield ``(workflow_name, command_string)`` for EVERY command the table
    names — steps AND see_also — so the registry-honesty test covers all of
    them, not just the numbered steps."""
    for name, wf in WORKFLOWS.items():
        for command, _why in wf["steps"]:
            yield name, command
        for command in wf["see_also"]:
            yield name, command


# ------------------------------------------------------------ CLI projections
# Plain data/strings only (no typer here): cli.py owns echo/colour, tests can
# assert on these directly.


def list_payload() -> dict:
    return {
        "workflows": [
            {
                "name": name,
                "title": wf["title"],
                "when": wf["when"],
                "step_count": len(wf["steps"]),
                "next": wf["next"],
            }
            for name, wf in WORKFLOWS.items()
        ]
    }


def detail_payload(name: str) -> dict:
    wf = WORKFLOWS[name]
    return {
        "name": name,
        "title": wf["title"],
        "when": wf["when"],
        "steps": [{"command": c, "why": w} for c, w in wf["steps"]],
        "next": wf["next"],
        "see_also": list(wf["see_also"]),
    }


def render_list() -> list[str]:
    width = max(len(n) for n in WORKFLOWS)
    lines: list[str] = []
    for name, wf in WORKFLOWS.items():
        lines.append(f"  {name.ljust(width)}  {wf['title']}")
        lines.append(f"  {' ' * width}  何时 when: {wf['when']}")
    return lines


def render_detail(name: str) -> list[str]:
    wf = WORKFLOWS[name]
    lines = [f"{name} — {wf['title']}", f"何时 when: {wf['when']}", "步骤 steps:"]
    for i, (command, why) in enumerate(wf["steps"], start=1):
        lines.append(f"  {i}. {command}")
        lines.append(f"     {why}")
    if wf["next"]:
        lines.append(f"然后 next: {wf['next']}  (manju help-workflow {wf['next']})")
    if wf["see_also"]:
        lines.append("另见 see also: " + " · ".join(wf["see_also"]))
    return lines

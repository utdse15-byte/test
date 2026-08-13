# ADR: Three-Project Integration in Strict Zero-Cost Mode

Status: accepted on 2026-08-11.

## Context

Manju is a deterministic production system whose project files remain truth.
ViMax, OpenChatCut, and Toonflow were reviewed at the exact revisions recorded
in `THREE_PROJECT_UPSTREAM_SNAPSHOT.yaml`. Their code was not executed, copied,
installed, or added as a dependency.

The licenses alone do not decide the architecture. ViMax's MIT license permits
reuse, but its autonomous provider pipeline conflicts with Manju's review and
truth boundaries. OpenChatCut is AGPL-3.0. Toonflow's current LICENSE combines
Apache-2.0 with supplementary distribution, commercial-authorization, and
branding terms. Clean-room method adaptation is the smallest reliable boundary
for all three.

## Decision

Use four layers:

1. **Manju truth and deterministic core.** Existing project files, domain
   services, checks, QC, build, export, locking, spend control, and evidence
   remain authoritative. No third-party repository is a runtime dependency.
2. **Thin Manju adapters.** CLI and GUI call shared Manju services. They do not
   translate third-party databases, execute third-party pipelines, or make
   reports, graphs, UI state, SQLite, or vector memory into execution inputs.
3. **Explicit local handoff.** Verified media and project bundles may cross a
   file boundary. OpenChatCut may be used as an independently installed,
   process-external, non-AI local editor. Any bridge is loopback-only and cannot
   restore Manju's removed agent protocol.
4. **Human authority.** People confirm truth, select candidates, approve edits
   inside OpenChatCut, and adopt finishing output. Models may propose but never
   select or Picture Lock.

Strict zero-cost mode forbids metered calls, free tiers, trials, provider keys,
real provider submissions, cloud inference/search/OCR/ASR/VLM/generation, and
automatic large-model downloads. It permits loopback fake providers,
FFmpeg/ffprobe, Python and existing dependencies, user-owned local media, and
manual non-AI local editing. There is no in-plan override.

All three upstream capabilities are optional. Their absence must not block
`manju check`, build, QC, or export.

## Adopt, Adapt, Reject

| Upstream | Adopt | Adapt clean-room | Reject |
| --- | --- | --- | --- |
| ViMax | None at runtime | Reviewable planning stages, explicit continuity dependencies, resumable local checkpoints | Source or pipeline reuse, autonomous end-to-end generation, provider configuration |
| OpenChatCut | Independently installed local manual editor | Draft/review/apply as a human approval concept; explicit file handoff | Copied code, Manju agent-protocol restoration, auto approval, generation/transcription/cloud features |
| Toonflow | None at runtime | Generic task-state and adapter-isolation concepts | Source/assets, provider VM, SQLite/task records as Manju truth or execution input, branding reuse |

## Consequences

Research remains auditable without license ambiguity or dependency drift.
Integration is less automatic, deliberately: a human and a verified file
boundary are required where authorship or irreversible editorial choice changes.
Any future paid-provider research requires a separate plan, budget, credential
environment, and approval; it cannot be activated by changing this ADR.

# Strict Zero-Cost Mode

Strict zero-cost means **no possible bill**, not "currently priced at zero."
Free tiers, trial credit, subscriptions, cloud accounts, and no-charge preview
endpoints are external paid-service paths and are forbidden in this execution
plan.

## Allowed execution

- Local files and media already owned by the user.
- Python and dependencies already present in the workspace environment.
- FFmpeg and ffprobe. Version 6.1.1 is a comparison baseline, not a downgrade
  requirement; reproduce and fix Manju-owned issues on newer versions.
- Fake providers over `localhost`, `127.0.0.1`, or `::1`, including loopback
  HTTP and local stdio fixtures.
- OpenChatCut as an independently installed non-AI local editor, with its
  endpoint on loopback and approval performed manually inside the editor.

The general `local_cmd` provider remains available in normal execution mode,
but strict zero-cost mode rejects it. An arbitrary child process can open its
own network connections, and Manju has no operating-system network sandbox
that can prove otherwise. Use audited in-process owners for FFmpeg/Python work.

## Forbidden execution

- Reading, requesting, configuring, or resolving any provider credential.
- Cloud inference, search, OCR, ASR, VLM, image/video/audio generation, real
  provider submit/poll/download, or any free-tier/trial/API call.
- Automatic model-weight downloads or unreviewed external package acquisition.
- Non-loopback provider, test, dogfood, or editor-bridge traffic.
- Automatic candidate selection, Picture Lock, truth confirmation, editor
  approval, or finishing adoption.
- Restoring Manju's removed agent protocol; copying upstream code/assets;
  executing a ViMax pipeline; or using report/UI/graph/SQLite/vector-memory
  state as execution input.

## Operator checks

1. Use a dedicated shell. Check common cloud-key environment variable names
   and report only `set` or `unset`, never values. Stop if any are set.
2. Set `MANJU_EXECUTION_MODE=strict_zero_cost` for every command in this plan.
3. Before any transport, parse the endpoint and require a loopback IP or the
   exact hostname `localhost`. Reject aliases, redirects, and unresolved hosts.
4. Keep request/evidence artifacts secret-free. Prove rejected egress has a
   transport count of zero.
5. At a manual gate, prepare evidence and stop before the guarded action.

There is no `manual_paid` mode and no zero-cost override. Paid research must be
designed later as an independent plan; no task in this edition may activate it.

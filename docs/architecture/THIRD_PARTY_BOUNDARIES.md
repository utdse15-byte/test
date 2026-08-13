# Third-Party Boundaries

This document governs ViMax, OpenChatCut, and Toonflow research. The pinned
evidence is `THREE_PROJECT_UPSTREAM_SNAPSHOT.yaml`; that YAML is disposable
audit evidence and never a runtime input.

## Clean-room rule

- Read only the minimum upstream files needed to understand a method, boundary,
  or license. Record every file actually read in the snapshot.
- Describe behavior in Manju terms, then implement only through an existing
  Manju owner. Do not translate, paste, vendor, subclass, import, or execute
  upstream code, prompts, assets, schemas, databases, or pipelines.
- Keep `external_repositories.runtime_dependencies` empty. Static governance
  checks cover `src/manju`, `skills`, and package metadata.
- A missing upstream checkout, application, or dependency is normal and cannot
  reduce the behavior of Manju check/build/QC/export.

## Process and data boundary

Manju core never starts or embeds any of the three projects. OpenChatCut is the
only permitted operational collaboration and only when independently installed
by the user: it is a process-external, loopback-only, non-AI local editor. Manju
hands off verified files and accepts output only through existing verification
and manual take registration. It does not share process memory, package graphs,
SQLite state, secrets, or provider configuration.

OpenChatCut draft approval stays inside OpenChatCut and is always manual for
this plan. Its auto mode is forbidden. Its external editor protocol is not a
Manju product surface; Manju remains files + CLI + GUI and does not regain the
removed agent client, server, skill, compatibility surface, or transport.

## Failure boundary

The boundary fails closed before I/O when an endpoint is not loopback, a secret
would be resolved, a real provider would be submitted, or a large model would
be downloaded. It also stops before truth confirmation, candidate selection,
OpenChatCut approval, or finishing adoption so a human can decide.

Third-party failures never mutate project truth and never trigger a cloud
fallback. The valid recovery is to continue using Manju's local deterministic
workflow, not to find a free tier or trial.

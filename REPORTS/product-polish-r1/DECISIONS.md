# Product Polish R1 Decisions

## Scope

The guide was treated as evidence, not a checklist. The repository already had
shared navigation, cockpit data, onboarding, review, export, app-window launch,
and zero-cost transport gates. Rebuilding those surfaces would add risk without
improving the daily loop.

## Evidence-led choice

The local `manju new --demo` project was opened through the real GUI server and
checked in the in-app browser at 1280x720 and 390x844.

- Warm `/api/state`: 9.9-12.7 ms; warm `/api/cockpit`: 96-101 ms.
- Before polish, the real build controls began around y=1407 px.
- The home page showed the cockpit's live six-step progress, then auto-opened a
  second full onboarding checklist, then displayed low-value empty telemetry and
  a long evaluation/honesty report before the build panel.
- The 390px pass had no horizontal overflow, so responsive CSS was not the first
  problem to solve.

## Changes

1. The cockpit remains the automatic guide. The full onboarding checklist is
   still available from the header/cockpit help action, but is no longer auto-
   fetched and rendered a second time.
2. Empty-ish projects stop the cockpit support grid after the progress checklist.
   Deliverable/queue/approval/evaluation details appear after real work exists.
3. Evaluation is a collapsed, professional-mode detail. Beginner mode does not
   fetch a panel it is explicitly hiding.
4. The project header removes its temporary spend/next-step fallback once the
   cockpit arrives, preventing normal-state duplication while retaining a
   useful fallback during cockpit loading/errors.
5. The current execution policy is carried in `/api/state` and shown as a small
   chip: `严格零成本`, `标准执行`, or `执行模式无效`. Unknown non-empty values
   fail closed before provider transport or credential inspection.

## Result

After polish, build controls began around y=641 px in standard mode and y=603 px
in strict zero-cost mode. The measured pages had zero horizontal overflow and
no browser console warnings/errors. Strict mode displayed the loopback-only,
credential-free policy directly in the UI.

Screenshots:

- `screenshots/before/home-desktop.png`
- `screenshots/before/home-mobile.png`
- `screenshots/after-home-desktop.png`
- `screenshots/after-home-strict-zero-cost.png`

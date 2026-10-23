# Sage Studio: unified conversation and member experience

Implemented 24 October 2026. The interface is English; ordinary assistant replies can follow English or Chinese input. Formal structured planning retains the existing English contracts.

## Member experience

The heading is **Your fitness assistant**. Front Desk, Coach and Nutrition Assistant have consistent portrait avatars with a contact card, dumbbell and meal icon. They are identified as AI assistants in the optional specialist menu. The composer has no request-type selector.

The foreground host and the work-item executor are separate. Viewing or explaining training and meals keeps the current host. A specialist choice records one handoff; choosing the same role does not create another. Historical message authors do not change. A slow inferred handoff cannot overwrite a newer explicit choice.

| Member intention | Result | Change to current plans |
| --- | --- | --- |
| View both plans | Current, member-scoped plan cards; a direct button needs no model call | None |
| General or hypothetical question | One structured intent-and-answer inference; current host remains | None |
| Deeper consultation | Optional specialist invitation or explicit role selection | None |
| Create or revise training/meals | Automatic delegation to the existing specialist workflow | Draft only |
| Revise training with linked meals | Explain the dependency and offer review of both, or discussion while keeping meals | No silent scope expansion |
| Confirm a proposed plan | Open the exact existing plan review and confirmation controls | Only after validation, any required approval and member confirmation |
| Record feedback or replace a food | Open the existing exact-plan feedback/substitution form | Only after its explicit submission/confirmation |
| Cancel or inspect progress | Open Activity with the actual update and cancellation confirmation | Existing plans remain |
| Update profile facts | Front Desk produces proposed facts for explicit review | No silent profile writes |
| Ask for a plan before admission | Preserve the goal until admission; resume only while unexpired and uncancelled | Draft only after admission |

A training-only replacement cannot activate against an incompatible linked meal plan. Choosing **Keep meals unchanged** closes that pending scope choice and continues discussion; it does not silently create a coordinated replacement. Already running conflicting changes must be cancelled in Activity before starting a replacement. Feedback, cancellation and food substitution use their existing precise controls rather than guessing an entity from ambiguous text.

## Presentation

- Sage colours, larger typography, consistent spacing and icons, gentle entry/focus transitions, device dark appearance and reduced-motion support.
- Plans show sessions, exercise names, sets, repetitions, duration, rest and technique notes. Meals show readable food names, quantities and expandable preparation.
- Nutrition quantities are recomputed from the food catalogue. They describe the displayed example, not a prescribed daily intake target. Missing totals are omitted.
- Current plans, drafts and history are distinct. Activity and plan review use the same business projection. An expired approval or draft is not presented as ready for activation.
- Activity replaces Requests in navigation: readable titles, **Needs you** and **In progress** filters, result links and cancellation confirmation. Completed generation is not equivalent to an active plan.
- The allowance card reads the real account dispatch ledger: 100 calls per rolling 24 hours, with 10 shared calls per rolling hour. A message can consume several calls. Viewing saved data uses none. Missing usage is an error state, never a fake zero.
- Unknown provider costs and reserved funds are retained. Available account allowance does not guarantee shared capacity or sufficient role budget.

## Implementation

`intent_router.py` runs a durable LangGraph workflow with typed output and the existing gateway, version checks, SQL publication and Redis checkpoints. Direct UI actions bypass inference. Free-text intent inference and specialist generation both count toward the same limits. Planning requires fresh admission, capability and baseline checks. A resolved follow-up goal is retained without extending the original deadline when the member accepts a pending scope choice.

`member_experience.py` exposes authenticated `/api/me/plan-summary`, `/api/me/activity` and `/api/me/usage`. The first reads only this member's current pointers and hides unusable plan content. It does not expose another member, raw profile records or owner budgets. The activity projection distinguishes drafting, studio review, activation, expiry, cancellation and incomplete coordinated work.

No schema migration is needed: the existing conversation host, immutable message authors, work-item roles and JSON request metadata carry the separate responsibilities. Existing request types remain API-compatible. Older records remain available. The quota ledger, unknown-usage holds and exact-version activation rules are unchanged.

## Verification

See the dated Sage Studio entry in [Acceptance evidence](ACCEPTANCE.md). Automated inference uses synthetic providers; it verifies routing and side-effect boundaries rather than claiming perfect natural-language classification. One live generic conversation uses a fresh fictional account without a profile, assessment, plan or previous conversation. Existing demo member plans are checked locally through deterministic view actions, without sending them to Google again.

The acceptance deployment is local to the workstation. GitHub stores the source; it is not the application hosting service. Private environment files, credentials and screenshots are excluded from source control.

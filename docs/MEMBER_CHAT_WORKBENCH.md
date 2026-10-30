# Member conversation workbench

## Interaction contract

The member keeps one conversation and one composer. Routine actions are completed in that conversation. Longer plan content, profile editing, activity and updates open in a dialog over the conversation. Closing the dialog restores focus and leaves the composer draft intact. Sidebar pages remain available for browsing.

All interface controls are English. Natural-language requests may be English or Chinese.

## Supported paths

| Intent | Conversation experience | Commit boundary |
| --- | --- | --- |
| Read training or meals | Current-plan cards open the selected type in a dialog | Read-only |
| Create/revise training | Coach discussion and exact training draft dialog | Endorse direction before meal review; final confirmation activates |
| Create/revise meals | Nutrition discussion and exact meal draft dialog | Final confirmation activates |
| Replace one or several foods in the active meal plan | Source meal/food/quantity and requested replacement preview; missing fields can be supplied in chat or edited in a dialog | Prepare replacement queues a draft; current meals remain active until final confirmation |
| Change a food in a draft | Continue the existing Nutrition draft discussion | Does not create an active-plan substitution |
| Record feedback | Explicit completion, difficulty when relevant, discomfort and notes | Save feedback records an observation, not a plan change |
| Review a cycle | Dates, counts, experience, difficulty, discomfort and desired direction in a preview | Confirm review prepares a draft; does not activate it |
| Review/confirm profile | Conversation summary with previously saved and proposed details | Explicit accuracy acknowledgement and existing assessment confirmation |
| Edit profile | Profile editor in a conversation dialog | Existing version checks and owner-review policy |
| Final plan decision | Current workflow card has Confirm update; full changes available in dialog | Existing activation service, owner approval and baseline checks |
| Cancel/read progress | Activity dialog or in-conversation cancellation confirmation | Existing cancellation service |
| Read allowance | Inline allowance card | Read-only, no model call for reading saved usage |
| Read updates | Updates dialog, with exact draft/profile targets; continuity reminder starts feedback preview | Mark read does not resolve approval or save feedback |
| Legacy draft | Exact saved draft dialog offers step-by-step discussion | Existing adoption guard; cannot adopt active work or another member's draft |

## Boundaries

- The model proposes an intent and structured fields. It cannot confirm a member action itself.
- Missing personal facts remain missing: discomfort, completion, dates and quantities are not invented. Ambiguous food/meal selection must be clarified.
- A new preview supersedes an older preview of the same kind in the same conversation. Superseded, dismissed and completed cards cannot execute again.
- Preview edits use revision checks. Successful confirmation is replay-safe and produces a durable receipt.
- Every write rechecks the authenticated member, current plan baseline and current usability. A changed baseline requires a fresh preview.
- Another open plan update blocks a new replacement or cycle review; the member must finish or cancel the existing update.
- Multiple selected food portions are processed in one replacement draft. Unselected meals and portions must remain unchanged. Full catalogue, nutrition and plan validation remain in force.
- Saving feedback does not alter a plan. Preparing a replacement or next cycle does not activate a plan.
- Training direction is reviewed before meal coordination. Modal navigation does not bypass workflow stages or owner approval.
- Failed actions display an error next to their controls and remain editable. Unconfirmed edits can be discarded explicitly.
- General questions and reading a plan should not be routed to mutation merely because they mention training or nutrition.

## Implementation

- `member_actions.py`: durable message-bound previews, revision guards, confirmation and receipts.
- `intent_router.py`: schema-constrained field extraction and pending-preview context.
- `feedback.py`, `substitution.py`, `cycle_requests.py`: reused transaction services; substitutions/cycles can retain the original conversation.
- `MemberActionCard.tsx`, `ProfileReviewCard.tsx`: preview, confirmation, before/after information and edit dialogs.
- `ConversationView.tsx`, `main.tsx`: member action shortcuts and dialog workspace without route changes.
- `WorkflowCard.tsx`: final confirmation through the existing activation endpoints.
- `Plans.tsx`: feedback/replacement/cycle entry points return to the same conversation.

## Acceptance evidence

Automated browser scenarios cover phone/desktop replacement previews, edit-before-confirm, durable completed state, cycle confirmation, exact plan dialogs, preserved composer input and training-before-meals progression. Backend integration scenarios cover ownership, replay, stale revisions, supersession, changed current pointers, unchanged active meals during substitution, original conversation retention and conflicting open work. These tests use isolated/synthetic evidence; they do not establish every live model response's quality.

### Verification run — 2026-10-30

- Backend full regression: 358 passed. After the final additional cases, focused member-action/substitution regression: 11 passed.
- Browser regression: 69 passed, including the isolated real API registration/intake flow; provider calls disabled for that flow.
- Production TypeScript/Vite build and Ruff checks passed.
- Existing local acceptance deployment: `http://127.0.0.1:8091/`; readiness HTTP 200, served frontend bytes and deployed action-service source matched the local build/source.
- Existing member account: opened the exact saved training plan and its before/after comparison in a dialog; URL remained the original assistant conversation. No existing plan was activated or changed during this check.
- Live Gemini/OpenAI interpretation quality for the new extraction fields has not been tested in this run. Automated intent/workflow tests use synthetic model output; human review should include natural-language ambiguity.

# Progressive interaction implementation and acceptance

Date: 2026-10-30. UI language: English. Explanation examples below are bilingual for manual acceptance.

## Implemented behavior

A member's training update switches the host to Coach. The validated training draft enters a discussion stage and has no queued Nutrition task. The member can ask questions, submit feedback in the same conversation, open the exact draft, or approve its direction. Approving direction records an exact draft fingerprint and advances the existing bounded request; it does not activate the plan.

After training endorsement, Nutrition checks the existing meals or prepares the requested initial meal draft. Retained meals are copied exactly and revalidated against the new training reference. A concrete meal conflict has two paths: discuss meal suggestions while preserving training, or preserve current meals and return to Coach. A nutrition-led conflict supports preserving current training or preserving the proposed meal direction and revisiting training. Each affected new direction must be approved again.

Independent training and meals skip irrelevant specialist stages. A selected-food substitution remains a short single-confirmation flow. Cycle feedback is still confirmed separately from the next training direction. Owner-requested revisions remain in the original request and invalidate affected member endorsements. Owner approval only opens the final member review; it does not activate a plan. Member Updates receives the review result and an exact draft link.

Profile/context changes invalidate endorsements and expose a deliberate refresh action. Original goals, generation call limits and deadlines are retained. Older cycle feedback and food selections tied to another baseline require a fresh review rather than silently rebinding evidence. Capacity recovery only resumes work already authorized by the current stage.

The conversation uses durable message ordering and stage cards at their actual point in the conversation. Completed legacy task summaries no longer accumulate at the bottom. Historical stage actions are disabled. The composer stays at the bottom; reading history does not force scrolling, and New updates returns to the latest result. Draft and pair routes show specific details without first displaying current meals.

## Compatibility and migration

The additive migration creates workflows and conversation_entries. Historical messages are indexed in stable chronological order; original messages, plans, approvals and billing are preserved.

There is no batch startup rewrite of planning work or approvals. A member can select `Discuss this saved draft step by step` in an idle, unexpired legacy draft's detail. The server rejects adoption if there is unfinished work. This is a deliberate change from the initial bulk-migration proposal. Existing completed conversations retain their historical wording; new goals use staged interaction. Historical expired requests are not revived.

The Workflow table adds stage, revision, immutable draft references, member endorsements, baseline and feedback. Existing Request/WorkItem rows continue to bound execution, maintain the original deadline/call allowance, and provide transactional durable dispatch. Existing owner configuration, release, budget, incident and unknown-usage services remain authoritative; the unified conversation and exact business pages keep their existing confirmation and permission boundaries.

## Manual member acceptance

1. Sign in as a member with accepted profile and active training/meals. Open the existing main conversation.
2. Send: `Please make my training easier to follow and keep each session within 30 minutes.`
3. Confirm the host changes to Coach and the next result is a training discussion. No Nutrition result should appear yet.
4. Send: `Please remove one exercise and allow longer rests.` Confirm this revises the same update. The old direction button becomes historical; no Activity cancellation is required.
5. Click `View training draft`. The detail must be that exact training version, with no current-food page above it. Return to the conversation; the draft input and position should remain.
6. Click `Continue with this training`. The text explains that this is direction approval, not activation. Only now should the nutrition step begin.
7. If meals are retained, confirm the final summary identifies unchanged meals. If meal adjustments are suggested, review the concrete changes before selecting a direction.
8. Try `Keep meals · Revisit training`. Coach must receive the meal constraint, issue another training draft, and wait for direction approval again.
9. For meal-led review, send: `Revise my meals to better fit my preferences.` When a concrete training impact is identified, verify both keep-training and keep-meal-direction choices refer to visible records.
10. After all directions and any required studio review, open the final pair and click `Confirm update`. Verify the current routine changes once and the update is completed.
11. Repeat with only training enabled / independent meals / a single selected food replacement. No unnecessary multi-specialist stages should be added.
12. Read older messages while a result arrives. The app should preserve scroll position and show New updates, with the newest result at the chronological end.

## Manual owner acceptance

1. Use a group requiring plan review. A draft under member discussion must not yet create an actionable owner approval.
2. After the member completes necessary directions, open Updates or ask `What needs my attention?` in the same Studio conversation.
3. Open the exact approval. Review the rendered training/meal contents and expandable validation evidence.
4. Request a revision with a specific reason. The member must see the reason and return to the relevant expert's discussion. A changed plan must not reuse the previous member endorsement.
5. Approve the resubmitted precise version. The member receives a review notification pointing to the exact draft; final activation remains a member decision.
6. Exercise existing budget/member-group/release/incident actions. Simple actions remain in their dialogs; complex evidence opens the correct business record and returns to the original conversation.
7. Increase a budget while a member is still discussing training. It must not cause a Nutrition call before direction approval.

## Automated evidence

- Full backend suite: 352 passed (`.local/progressive-backend-final.log`). Uses isolated SQL/Redis and synthetic inference where model work is required.
- Full browser suite: 63 passed (`.local/progressive-browser-final.log`). Covers retained owner pages, member basics, exact plan navigation, and new desktop/mobile staged flow.
- Final focused backend acceptance: 31 passed (`.local/progressive-acceptance.log`).
- Additional stage/adoption/provider boundaries: 24 passed (`.local/progressive-boundaries.log`).
- Final browser checks: 8 passed (`.local/progressive-ui-final.log`).
- Ruff, TypeScript/Vite build and diff whitespace checks are required before publication.

Provider behavior is not validated by synthetic fixtures. No paid model calls are required for these automated checks; a live provider acceptance run must be identified separately with model, cost and data scope. General-plan compatibility review is an AI-supported judgment plus structural/dietary validation; it is not a clinical determination of energy adequacy.

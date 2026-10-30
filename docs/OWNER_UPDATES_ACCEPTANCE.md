# Owner Updates and visible editing

## Experience

- One continuous Studio assistant conversation remains. Existing business pages remain pages.
- Updates is the shared owner decision inbox. The sidebar count means unresolved **Needs action**, not unread messages.
- A persistent top banner links to Updates. A new-item toast links to the same inbox; dismissing it does not dismiss work.
- The foreground app checks for changes every 10 seconds and on focus/reconnect. Scheduled reconciliation runs every minute. This is in-app notification delivery, not email or OS push.
- Approvals, approved changes awaiting execution, incidents, uncertain provider usage, paused requests, missing member spending setup and evaluation review/results are discovered from authoritative records.
- Identical budget blockers are grouped by scope. Reading does not resolve them. When the underlying condition disappears, the item moves to Resolved. Resolution means no longer actionable; open the outcome to distinguish completion from cancellation/expiry.
- Simple approvals, budgets, group reassignment and call allowances use shared existing services. Short editors open a focused modal. Complex evidence opens the exact record and preserves the main conversation.
- New members without a personal monetary cap produce a setup reminder. Money is **not silently allocated**. Owners set the cap explicitly. Existing monetary caps and uncertain reservations are preserved.
- Updating budgets triggers the existing bounded recovery check. Work already dispatched or with uncertain billing is never replayed. Already expired work stays expired.

## Manual acceptance

1. Sign in as owner. Open **Updates**. Verify counts and icons; **Mark as read** must not lower the action count.
2. On a member account submit an application requiring review. Within 10 seconds in the open owner app, observe the new reminder. Click **Review now**.
3. Review the exact application in the card. Record a decision. Verify the card leaves Needs action and the original review record agrees.
4. For a request blocked by spending, select **Adjust budget**. Verify the dialog identifies the correct scope, existing spending and reservation. Enter a new total, reason and confirm.
5. Verify the original saved request resumes only when every remaining gate permits it. Any remaining blocker stays in Updates; inspect each without resubmitting the member request.
6. Open **AI usage & limits → Spending controls → Edit spending limit**. Verify the dialog opens in the current viewport even after scrolling a long list. Cancel must not save.
7. Repeat from **Advanced controls** for a member/group. Restore your original cap after testing if appropriate; changing limits does not reset usage.
8. In the one main conversation ask **What needs my attention?**, **Show requests blocked by spending limits.**, **Show approved changes that still need to be applied.**, and **Show updates.** Verify real cards and exact links, with no provider call for discovery.
9. Open a complex incident/release/evaluation record, then **Back to conversation**. History and draft remain.
10. Test mobile (390px), keyboard Tab/Escape, duplicate save clicks, an old approval already decided elsewhere, and a disconnected browser that reconnects.

## Boundaries

Backend source changes are required for new decision-producing workflows to join this inbox. The current sources are enumerated above. The app cannot deliver a live banner while closed; persistent business records are reconciled on return. Reviewed evaluation results are informational, not a demand to release a model.

## Verification recorded 2026-10-30

- Existing backend regression: 343 passed.
- Final focused backend checks (inbox lifecycle, grouped blockers, recovery, owner conversation): 37 passed.
- Browser regression including new 390px / 1440px inbox-dialog flows: 56 passed.
- TypeScript production build and application Ruff checks passed.
- Live local acceptance: 7 outstanding owner items were discovered from existing records, including the UAT member's missing cap and Default group's blocked spending. Marking the member reminder as read retained count 7; Adjust budget opened the exact member modal. Existing financial limits were not changed during this check.

## Historical uncertain usage acknowledgement

- Each uncertain provider call has one Usage review card. Its associated usage-reconciliation incident is not a duplicate action in Updates; the incident remains available in Incidents.
- `Acknowledge & archive` opens a confirmation dialog in both the conversation and Updates. Confirmation moves the item to Monitoring, removes it from the action count and suppresses repeated new-update toasts across reloads.
- The audit event is durable and shared across owner sessions. Repeated confirmation is idempotent. A different uncertain call creates a new decision.
- Acknowledgement does not settle charges, release reservations, restart tasks, close incidents, or claim service recovery. Actual provider-charge reconciliation remains a separate accounting workflow; this change does not add a manual charge-entry form.
- If the underlying usage is subsequently resolved, normal inbox synchronization moves the item to Resolved.
- Acceptance: owner/member permission boundary, duplicate incident suppression, repeat acknowledgement, repeated synchronization, immutable unknown billing and reservation; browser modal, badge removal, reload and Monitoring visibility.

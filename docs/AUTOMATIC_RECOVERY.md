# Automatic request recovery and member status

## Member experience

A member keeps one conversation and one original request. Capacity delays require no cancellation or resubmission. The task card and Activity update from the server; a background tab refreshes its snapshot on focus, reconnection or visibility change. Polling provides a five-second fallback in the conversation and Activity. SSE invalidates snapshots without applying historical status events to the UI.

| Situation | Public status | Member action |
| --- | --- | --- |
| Internal spending or shared capacity prevents dispatch | Temporarily delayed | None; resume automatically after all checks pass |
| Personal daily allowance is exhausted | Waiting for your allowance | None; resume when allowance becomes available |
| Policy or profile requires review | Awaiting studio review | None while the studio reviews; no automatic bypass of changed inputs |
| A dispatched result is uncertain | Checking your request | None; studio reconciliation is required |
| Original task is requeued | Resuming your request / Resuming your plans | None |
| Drafts are ready | Ready for review | Review and explicitly activate |
| Original task expired or was cancelled | Expired / Cancelled | Never revive automatically |

Public explanations describe service availability and next steps. Internal amounts, provider failures, financial scope keys and raw wait reasons are excluded from member request projections and event payloads. Personal call usage remains visible because it is the member's own allowance. Operational evidence remains available to the owner.

The composer remains available during a background wait. Identical unfinished free-text submissions in the same conversation reuse the pending request, with idempotent replay; direct plan viewing still works.

## Backend behavior

- `WorkItem.wait_context` preserves the exact failed reservation amount and model for new waits. Existing waits without this data use a conservative model-capacity bound.
- `resume_after` and `resume_count` persist bounded backoff across restarts. `capacity_waits` keeps total attempt history intact while excluding pre-dispatch capacity waits from the crash retry bound. Maintenance runs at the existing scheduler interval (default 60 seconds), with checks backing off to at most 300 seconds between attempts. This is not a promised completion time.
- Budget, call-policy, admission, group assignment and group-configuration commits trigger checks after commit. The same shared services handle webpage and owner-chat changes. A failed recovery check cannot roll back a successfully saved setting; maintenance retries it.
- Recovery locks the existing work item and request, rechecks deadline, account, policy, input versions, model credentials, every applicable monetary scope and current rolling quotas. Dispatch still performs atomic financial and Redis admission checks.
- A recovery event invalidates member snapshots. Successful stages, original inputs, request ID and deadline are preserved. No new member message is created.
- Capacity waits do not consume crash-recovery attempts. Undispatched cancelled reservations restore the per-request call allowance; dispatched or uncertain calls never do.
- Requests with Reserved, Dispatching or UsageUnknown calls cannot resume automatically. Releasing a local execution slot does not reconcile billing or authorize redispatch. Unknown calls remain subject to the existing studio review workflow.
- Settings changes do not clear historical usage, refund reservations, activate drafts or bypass approval.

## Database rollout

Apply Alembic revision `f261030c001` before starting the new API, worker and scheduler. It adds recovery metadata to work items and preserves existing budget, call and audit records. Deploy the frontend together with the backend. Existing expired requests remain expired.

This change's test environment is isolated from the acceptance environment. It does not silently change the owner's saved $40 limit or settle historical Gemini calls.

## Acceptance protocol

1. Submit one plan-generation request with insufficient internal capacity. Verify a public delay explanation and **No action needed**, without financial details or a continuously spinning unfinished stage.
2. Increase only the global limit while leaving another applicable scope insufficient. Verify the original request remains waiting.
3. Make every required scope sufficient through the owner settings or chat action. Verify the same work/request is queued once and its card updates automatically.
4. Verify the result appears without another member POST, and an unsent composer draft survives.
5. Verify repeated identical input reuses the original pending task, while direct plan viewing remains accessible.
6. Verify cancellation, expiry, changed profile versions and unknown provider usage prevent inappropriate recovery.
7. Verify eight concurrent recovery attempts create only one recovery event and retain one original work item on MySQL.
8. Verify member HTTP history and SSE payloads omit internal financial evidence; member requests to owner budget APIs remain forbidden.

Automated tests use synthetic provider outputs or mocked HTTP/UI APIs. They do not spend provider credit. UI tests demonstrate the frontend state transitions; database tests separately verify the real shared service and queue behavior.

## Verified in this change

- Fresh isolated MySQL 8.4 and Redis 8.2: all migrations through `f261030c001`, followed by **343 passing backend tests**, including eight concurrent recovery attempts. Test containers and their test-only volumes were removed afterward.
- Browser regression: **26 passing tests** covering automatic recovery, cancellation, SSE replay, owner workspace, limits and responsive Sage pages. After the final copy changes, the six directly affected recovery/limits cases passed again. Browser APIs were mocked; this is not a live paid-provider test.
- Production frontend build, Ruff and whitespace checks passed.
- The running acceptance service on port 8091 and its database have not been deployed or migrated by this code-editing task. No historical unknown charges were settled or released.

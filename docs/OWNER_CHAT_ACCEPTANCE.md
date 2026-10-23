# Unified owner workspace acceptance

Date: 24 October 2026

## Delivered behavior

The owner uses one **Studio assistant** conversation for all business areas. Membership, review, configuration, budget, recovery, comparison and release cards have distinct icons and labels. Existing sidebar pages remain ordinary lists, editors and detail views.

| Flow | Delivered interaction |
|---|---|
| Attention | Authenticated pending reviews, eligible approved changes, incidents, uncertain costs and member confirmations have separate responsibility labels. |
| Review decisions | Expand exact local evidence in chat; approve, request revision or reject using the original service checks. Completed decisions produce durable receipts. |
| Member groups | Resolve actual members and groups; preview a unique destination, supply a reason and explicitly confirm reassignment. |
| Budget changes | Show current and proposed USD limits, spend and reservations; require a reason and confirmation. Call limits are unchanged. |
| Simple follow-up | Read notifications, cancel eligible own activity, release eligible stopped slots while retaining funds, and apply eligible approved group changes. |
| Detailed work | Open exact configuration, incident, candidate, comparison, release or trace records. Linked review and recovery steps avoid manually searching a second list. |
| Reporting | Show live summary metrics and open the reporting section for UTC dates and local download. |
| Return navigation | Preserve the same conversation, message history, composer draft, inline draft and reading position. Exact links survive authentication and refresh. |

Common English and Chinese owner requests use local record lookup and typed cards. Unresolved intent returns workspace choices. An explicit amount or unique destination can prefill a draft; free text never executes a mutation. Explicit incident AI analysis continues in the same conversation using the existing model and budget gates.

## Automated verification

- Backend suite: **292 passed** using isolated MySQL/Redis and synthetic or disabled provider transport.
- Browser suite: **46 passed**, including the existing isolated-API member smoke flow.
- New coverage: 15 owner backend cases and 12 owner browser cases.
- Production frontend build, Ruff and whitespace checks passed.

New tests cover bilingual lookup without model dispatch, owner authorization, approval versus member responsibility, invalid receipt attempts, exact approval fingerprints, receipt replay, stale-budget conflicts, explicit draft confirmation, superseded applications, unique group selection, one persistent composer, detail refresh, draft retention, responsive widths, pagination beyond 50 records and delayed initial-conversation loading.

The full regression also verifies existing comparison, release, rollback, recovery, cancellation, permissions and trace-export behavior. A regression in export audit correlation was corrected so exporting a snapshot does not invalidate that snapshot's replay fingerprint.

## Deployment verification

The local acceptance deployment uses the real API, worker, scheduler, MySQL and Redis. Runtime source files match the checked-in implementation. Existing credentials, data volumes, global monetary budget and model-call limits are preserved: 10 shared calls per rolling hour and 100 calls per account per rolling 24 hours.

The new owner reads and business actions do not call Google. Live provider calls and external trace export were not triggered for this acceptance. Detailed release and recovery execution was covered in isolated regressions rather than changing the existing local deployment's active configurations.

A real-browser run against the deployed service also passed: fictional member submission → inline owner approval → automatic member refresh; group destination prefill and confirmation; scoped budget prefill and confirmation; persistent receipts; exact-version detail navigation and reload; composer/form draft retention; and 320/768/1440 pixel layouts. These workspace requests recorded zero model calls and no browser errors. A further smoke check passed both existing account logins and all 13 member/owner destinations. Interrupted fictional applications were closed after acceptance; the original member application was not changed.

Private browser evidence and credentials remain under ignored `.local/`; they are not published in the repository. GitHub contains source code; the acceptance service is local, not a public hosted deployment.

## Manual acceptance

1. Sign in as the owner and open **Assistant**.
2. Ask **What needs my attention?** or **现在有什么需要我处理？**.
3. Expand a specific review card, inspect its evidence and choose the intended decision. The result appears in the same conversation.
4. Ask for a specific member or budget. Review the proposed change before using its labelled confirmation button.
5. Open an incident, comparison or release from its card. Verify the exact record, then use **Back to conversation**.
6. Open **Members**, **Review queue** or another sidebar destination directly. These remain conventional business pages operating on the same records.

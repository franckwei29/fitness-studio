# Dual-provider acceptance — 2026-10-30

## Delivered behavior

- OpenAI Luna and Google Gemini are selectable in the owner Agent versions page.
- The local acceptance environment defaults to OpenAI `gpt-6-luna`.
- Every provider/model has a rolling 100-call hourly limit. Accounts share a rolling 200-call daily limit across providers and roles.
- Provider, response cap, and reasoning settings are validated and versioned. Saving a candidate does not release it or dispatch a paid call.
- Owner budget controls show separate model usage cards. Member Activity shows the account allowance.
- Historical Gemini versions, calls, approvals, plans, and existing business pages remain readable.

## Automated evidence

| Check | Result |
| --- | --- |
| Backend full regression | 310 passed |
| Browser full regression | 48 passed |
| TypeScript and production build | Passed |
| Ruff and whitespace check | Passed |
| Tracked/new source credential-pattern scan | No credential matches |

Coverage includes provider mismatch rejection, immutable output/reasoning options, strict structured responses, refusals and incomplete outputs, sanitized single-attempt errors, cache-read/write and reasoning accounting, independent model-hour limits, the shared 200-call account window, concurrent quota admission, legacy quota seeding, and return of undispatched permits. Browser coverage includes provider switching, candidate settings, owner usage cards, and mobile layout, alongside the existing member and owner workflows.

## Real OpenAI evidence

Only fictional demonstration data was used. Private request/response evidence and account credentials stay under the ignored `.local/` directory.

1. The authorized OpenAI credential could access the exact model `gpt-6-luna`.
2. A bilingual chat and a structured intake test completed and validated locally.
3. An explicit 30-minute cache request completed. Its returned cache-write counts were charged at the documented write rate, with reasoning counted inside output only.
4. The deployed member chat completed through the real worker and gateway. Its account count increased by the recorded number of model calls.
5. A combined training/nutrition request used two Luna calls and completed with zero correction rounds. Both components passed the existing deterministic business checks and produced reviewable drafts; existing active plans were retained.
6. After final accounting changes, another deployed chat completed and incremented the daily account counter correctly.
7. Saving an OpenAI candidate preserved the live group configuration; a mismatched Gemini/OpenAI combination was rejected.

The pre-release acceptance identified a missing cache-write price component. It was corrected before delivery, tested with actual returned usage, and the three affected local acceptance ledger entries were adjusted by exact deltas with audit events. Existing unknown Gemini charges remain reserved. An expired local execution slot was released through the existing audited owner endpoint; no uncertain bill was treated as free.

## Scope and limits

- This iteration's real paid-provider verification used Luna. Gemini transport and historical behavior passed regression coverage; a fresh paid Gemini quality comparison was not run.
- The catalog currently enables Luna and the existing Gemini Flash Lite model. Other model IDs require capability and pricing support before appearing in the selector.
- The app's budget is distinct from the provider account balance. This change does not automatically spend or set a budget equal to the reported $100 platform credit.
- The acceptance URL is a local deployment; it requires this Mac and Docker to be running.
- Automated evidence does not substitute for the owner's quality review when publishing a separately configured role candidate.

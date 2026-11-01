# Meal replacement and context repair acceptance — 2026-11-01

## Changes

- Food substitutions copy the original meal structure and apply only selected portions and their meal preparation text. Full nutrition, dietary, scope and baseline-version validation still runs before publication. Numerically unchanged portions are rejected, including `50` versus `50.0`.
- Active-plan replacement requests in chat use the same preview and confirmation path as the Replace food button. Open-draft edits remain in their existing workflow.
- OpenAI generation uses the official input token count endpoint before reservation and dispatch. The 64,000-byte JSON gate is removed. Input allowance is 1,050,000 minus the configured output cap and 25,000 tokens of headroom. No automatic truncation or summarization is introduced.
- Capacity errors and token-count service failures have separate public and owner explanations. Only service failures auto-retry; capacity errors do not promise automatic recovery.
- Long-context accounting applies the above-272,000-token price tier. Existing monetary budgets and call limits remain enabled.
- The owner dashboard selects only display fields when sorting recent requests; it no longer sorts large request JSON that exhausted MySQL's sort buffer during regression.

## Automated checks

- Backend full regression: 405 passed, 2 failed initially. Both failures were the dashboard sort-memory problem above.
- After the fix: 48 focused tests passed, including both failed dashboard tests and new context/substitution checks. A final focused pass after completing error descriptions passed 41 tests; 16 combined-plan, standalone-nutrition and member-action integration tests also passed. These counts overlap and are not summed.
- Frontend: 91 of 92 browser tests passed on the first run; the registration test lacked its test API. After starting the isolated, model-disabled API, that test passed (1/1). All 92 cases therefore passed across these runs.
- Frontend production build, Python compilation and `git diff --check` passed.
- Deterministic checks cover multiple selected portions, unchanged other meals/portions, no-op replacement, unknown food, milk restriction, stale baselines, duplicate confirmation, token capacity boundary and long-context settlement.

## Real-provider acceptance in a separate database

A new test member confirmed their profile, generated a two-day training plan and meal example, and explicitly activated the pair. No production/member data was used.

| Entry | Case | Result |
| --- | --- | --- |
| Real browser, Replace food | Breakfast apple → peeled raw banana | Passed; other portions/meals identical; explicit final activation |
| Real browser, Replace food | Lunch cooked white rice → cooked brown rice | Passed; other portions/meals identical; explicit final activation |
| Real browser, Replace food | Lunch chicken → lean raw-weight sirloin steak with cooking instructions | Passed; other portions/meals identical; explicit final activation |
| Conversation API + real model | View entire current plan, then Dinner chicken → cooked kingfish | Passed; correct dinner target and preview, other portions/meals identical |
| Chinese conversation API + real model | Dinner cooked white rice → boiled potato | Passed; correct dinner target and preview, other portions/meals identical |
| Open-draft conversation | Add 150 g peeled raw orange to Snack | Passed after a quantity clarification; same workflow, new draft version, active plan unchanged; draft subsequently cancelled |
| Ambiguous conversation | Ask about replacing chicken without a definite replacement | Passed: no silent plan activation or automatic substitution execution |
| Large conversation | Complete plans plus three pending action previews | 81,602-byte request completed; provider reported 19,999 input tokens; call settled |

A cooked-salmon request missed the local search and its external web search did not complete. It was cancelled without activating a plan. Unknown billing and its reservation were retained. After the existing five-minute guard, the normal owner endpoint released only its local execution slot. The successful fish case used the confirmed local `Kingfish, cooked` entry. This is a remaining external-lookup reliability limitation, not a successful salmon test.

Microphone/audio recognition was not tested; conversational tests submitted text equivalent to dictated input. No million-token generation was performed; large-capacity boundaries and pricing tiers were verified synthetically.

## Environments

Acceptance project: `fitness-repair-accept`, private `.env.repair-accept`; stopped with its volumes retained after testing. Private evidence is under `.local/repair-*.json` and screenshots under `.local/repair-*.png`.

Delivery project: `fitness-repair-fresh`, private `.env.repair-fresh`, web address `http://localhost:8088`. Its database is independent of acceptance and the previous `fitness-v1` project. Old application services are stopped, with their database and Redis volumes retained. Credentials are kept in ignored private files, never in this document or Git.

Restart the delivery instance with:

```sh
docker compose -p fitness-repair-fresh --env-file .env.repair-fresh -f compose.yaml -f compose.app.yaml up -d --no-build
```

No commits or pushes were made; prior uncommitted project changes were preserved.

Final delivery verification: readiness, owner login and owner dashboard each returned HTTP 200. Model calls are enabled. The fresh database contains 1 owner, 0 members, 0 requests, 0 messages, 0 plans, 3,741 food records and 99 exercise records.

## Follow-up search and progress fixes

Search requests allow up to three built-in tool calls. Completed searches with cited text can survive an incomplete auxiliary page action; missing evidence remains a failure. Search accounting prefers reported billable search counts and does not count page opens as searches. Twenty-four OpenAI provider tests passed after this change. Frontend progress distinguishes running, queued and paused work; eight member/owner browser checks passed. Images rebuilt and owner login/readiness verified.

The final clean delivery instance is `fitness-delivery-clean` with private `.env.delivery-clean`. It uses independent volumes and contains only the initialized owner and catalogues. Previous delivery volumes remain preserved. Historical uncertain calls were not fabricated as settled.

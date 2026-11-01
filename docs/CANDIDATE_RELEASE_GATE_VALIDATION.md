# Candidate-only release checks — 2026-11-01

Release eligibility now requires the candidate to pass every defined case. Baseline rule failures remain visible and do not block release. Both sides must still have complete, authentic provider evidence: pinned inputs, matching output and checks, settled usage, and consistent task provenance. Proposal, confirmation and execution revalidate that evidence. Existing FailedChecks records are evaluated with these rules without rewriting stored results.

The comparison detail endpoint returns authoritative `release_eligible` and `release_block_reason`; the UI uses them instead of inferring eligibility from the overall comparison status. FailedChecks remains an accurate description when either side fails.

## Validation

- 30 backend tests passed in an isolated, newly initialized MySQL/Redis environment. Protocol simulations cover baseline-only failure through proposal, confirmation, Canary execution and expansion; candidate failure rejection; tampered or missing evidence; comparison lifecycle and decision handling.
- 7 browser fixture tests passed, including baseline-only failure permitting a proposal and candidate failure hiding the release action.
- Frontend build, targeted Ruff checks and diff whitespace checks passed.
- Real browser chat on a separate new database (`fitness-studio-gate-verify`), using existing credentials and limits:
  - `ebc2b07a-b040-45f3-9bd6-bc5aa26dec46`: GPT 2/3, Gemini 2/3. Release action absent, correctly blocking the candidate. The initial success-path harness timed out waiting for that intentionally absent button; no result was altered.
  - `e21fe364-9025-4dce-a3cc-2c345a8ba9ee`: GPT 2/3, Gemini 3/3. Browser successfully prepared a Canary release. No real release was confirmed.
  - 12 real evaluation calls, all settled, no unknown usage. Evaluation cost $0.013110, excluding management chat calls.

## Clean handoff

Final project: `fitness-studio-gate-final`, private ignored environment file `.env.gate-final`, URL http://127.0.0.1:8088. Separate new MySQL and Redis volumes; normal bootstrap only. Previous user and verification databases remain retained and their services are stopped. Local detailed evidence is in `.local/comparison-validation/gate*.json` and screenshots.

Restart from the repository root:

```sh
docker compose --env-file .env.gate-final -p fitness-studio-gate-final -f compose.yaml -f compose.app.yaml up -d --no-build
```

Model outputs can still fail the defined checks; this change fixes release gating, not generation quality.

## Final keep-current decisions

Saving Keep current model closes that comparison. The UI removes the release action immediately and after reload, and the evidence gate rejects release proposals, confirmations or execution for a closed comparison. Release preparation locks the same evaluation row as keep-current, preventing conflicting decisions from being accepted concurrently. Reconsideration requires a new comparison.

The closing-decision regression passed 19 backend tests and 7 browser tests. A real browser also verified the user's saved decision on the current deployment without changing its data or making model calls.

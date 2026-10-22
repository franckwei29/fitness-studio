# Acceptance evidence and remaining gates

This record distinguishes implementation checks from complete acceptance. It is not a declaration that all requirements are finished. Tests using synthetic model outputs do not prove live Gemini behavior. A successful page load does not prove every action on that page.

## Current evidence

- The consolidated backend run at source revision `b7e9c57` completed with **214 passing tests**. This count is not a claim of complete scenario coverage.
- The isolated backend suite exercises real MySQL transactions and Redis task state with provider calls disabled. Run it with `.venv/bin/python scripts/test_backend.py` from the application root.
- Browser scenarios live in `frontend/e2e`; run `npx --no-install playwright test` from `frontend`. Most use explicit API presentation fixtures. `member.spec.ts` uses the local application. Tests state their evidence boundaries.
- A separate local acceptance environment has six settled Gemini calls across Intake, Coach, Nutrition and Technical Manager. Its recorded total is US$0.008749. This is that environment's total, not an account-wide billing statement. The requests cover Chinese intake, coordinated planning including one invalid-candidate correction, Chinese-input English-output Coach explanation, and a separately authorized read-only operations explanation.
- Real local member and owner sessions loaded 13 navigation destinations without page errors. This verifies basic authenticated navigation, not every mutation.
- A committed source archive at `7e5db09` started in a new directory with new MySQL and Redis volumes and no model key. Owner login, member registration, confirmed intake and readiness passed. Its API, worker, scheduler and Redis were restarted; the session, messages, waiting task, original deadline and single attempt survived, with zero model calls. Later source revisions still require the final clean-source main-flow check.
- Credentials, private runtime evidence and screenshots remain outside tracked source. No API keys or demo passwords belong in this document.

## Scenario mapping

The named checks establish the stated scope only. The open gates below remain mandatory.

| ID | Evidence and verified scope | Remaining boundary |
|---|---|---|
| T01 | Admission integration and live fictional-member intake: explicit confirmation, unique membership and profile snapshot. | Final clean-source main flow. |
| T02 | Admission/rules tests reject missing and contradictory facts; owner approval is bound to current facts. | Covered by named automated checks. |
| T03 | Rules/admission tests cover each hard exclusion; handoff tests block saved and queued services for all five. | Covered by named automated checks. |
| T04 | `test_owner_approval_requires_exact_current_facts` checks the discretionary admission branch. | Include the owner review interaction in final acceptance. |
| T05 | `test_failed_task_persistence_preserves_admission_and_retry_is_unique` injects a work-item persistence failure, retains membership and retries the same request once. | The handoff is an explicit planning submission after admission. |
| T06 | Admission tests check immutable profile history, confirmation and stale assessment rejection. | Covered by named automated checks. |
| T07 | Admission handoff tests retain historical plans but mark them unusable and reject queued inference after a confirmed exclusion. | Covered by named automated checks. |
| T08 | Training validators and combined workflow tests check catalogue IDs, equipment and time; live Coach produced a validated candidate. | Recheck user-facing quality of the final active demonstration plan. |
| T09 | Standalone nutrition lifecycle covers enabled/disabled training, approval, activation and substitution; live Nutrition produced a paired meal example. | Independent nutrition has synthetic-model integration evidence, not a separate live call. |
| T10 | Combined workflow tests exercise repeated invalid candidates and shared correction/call limits; live planning retained one invalid candidate before correction. | Covered by named bounded-correction evidence. |
| T11 | Combined workflow checks direct revision routing and stale baseline rejection; planning-feedback tests use exact-plan confirmed observations. | Revision explanations and diffs require human content review. |
| T12 | Cycle validation rejects incomplete or inconsistent self-report; combined workflow exercises maintenance/progression, with deload covered by rule tests. | Final member cycle-review walkthrough. |
| T13 | Combined workflow and live pair confirmation show training then nutrition and atomic activation. | Final clean-source main flow. |
| T14 | Combined workflow retains old active pointers until the new coordinated pair is confirmed. | Covered by named integration checks. |
| T15 | Combined workflow exercises validated nutrition retention with new compatibility evidence and no extra model call. | Covered by named integration checks. |
| T16 | Activation tests inject transaction failure and race two confirmations; both pointers and receipts roll back or commit together. | Covered by named database checks. |
| T17 | Standalone and combined checks enforce required approval; expiry handles pending approvals. | Complete a plan-type decision matrix for Approve, Revise, Reject and Expired. |
| T18 | Admission, configuration, activation and evaluation tests reject stale fingerprints, versions and baselines. | Cross-check every approval subject against this invariant. |
| T19 | Repeated approval/configuration execution and concurrent activation are tested; actual service restart preserves state. | Concurrent approval and restart-after-approval require targeted evidence. |
| T20 | Ownership tests and role policy restrict member records, fields and tools; field-permission browser and integration tests exercise configuration. | Confirm the denial audit requirement for each protected mutation. |
| T21 | Policy rechecks run on claim/execution; suspension tests stop queued work before inference. | Explicit waiting-task permission-revocation/resume matrix. |
| T22 | Configuration tests enforce role boundaries, approval and stale-baseline rejection; schemas enforce capability prerequisites. | Covered within these constraints. |
| T23 | Release tests reject missing or unreviewed evidence; evaluation integrity tests check input/output/usage provenance. | Full successful evaluation-to-release integration path. |
| T24 | Database rollback tests restore retained versions or record degradation on conflicting configuration. | Full successful release followed by monitored rollback. |
| T25 | Worker tests record waits without dispatch when models are disabled; gateway/work-queue tests preserve uncertain provider outcomes. | Inspect optional incident-specific analysis, beyond aggregate operations explanation. |
| T26 | Rollback recovery tests require fresh approval, preserve newer settings and require a subsequent matching run before service recovery. | Explicit recovery requiring both configuration and version-change evidence. |
| T27 | Incident tests reject false recovery; unresolved closure and rollback restoration remain distinct from service recovery. | Covered by named incident/rollback checks. |
| T28 | Budget integration races reservations against a shared allowance. | Covered by real database concurrency checks. |
| T29 | Disabled-provider/zero-budget paths and clean deployment make no paid calls. | Confirm all role and evaluation paths use the same gateway. |
| T30 | Budget and queue tests retain unknown usage, prevent redispatch and make settlement idempotent. | Unknown provider usage is not inferred as zero. |
| T31 | Operations tests reconcile counts and costs; authenticated overview loads recorded data. | Covered within tested periods and scopes. |
| T32 | Canary tests scope observations to version/group; expansion rules reject insufficient observations; rollback tests mutate real configuration. | Full approved canary-to-observation-to-expansion flow. |
| T33 | Notification tests deduplicate, honor switches and count confirmed training/cycle feedback; navigation does not manufacture feedback. | Covered by database and browser checks. |
| T34 | Substitution rules and combined/standalone lifecycle tests enforce selected-portion scope and current baseline. | Include no-supported-food and approval outcomes in final scenario review. |
| T35 | Operations-period tests use one summary service for dashboard and downloaded brief. | Covered by integration and browser checks. |
| T36 | Database cursor replay and native EventSource reconnection test ordered deduplication and reject late snapshots; local real cancellation updated the page. | Covered by these replay checks. |
| T37 | Queue tests fence stale claims and retain unknown calls; clean-source service restart preserved a waiting task and session. | Finish the crash-window audit, including post-provider/pre-commit boundaries. |
| T38 | Policy filters and redacted export tests exclude profile/text/secrets; graph execution suppresses raw external tracing. | Inspect final actual model payload boundaries; external LangSmith ingestion remains optional and not live-verified. |
| T39 | All four roles have settled live Gemini records with recorded usage. | This does not prove every optional workflow or model-quality criterion. |
| T40 | Clean source and empty volumes passed startup/login/intake/restart. | Latest-source complete member/owner main flow still required. |

## Completion gates still open

1. Close the explicitly incomplete rows above with concrete implementation or evidence; do not treat test count as requirement coverage.
2. Finish final demonstration-plan quality review, including a historical Coach limitation that incorrectly described nutrition as unavailable. Preserve historical output rather than silently editing it; any replacement must follow normal generation and confirmation.
3. Run the latest-source clean deployment through the complete main flow and verify the intended worker configuration for interactive acceptance.
4. Finish English UI and error-state review, including candidate, approval, waiting, stopped and unavailable states.
5. Publish the final source revision to the intended GitHub repository through the authorized publication process. Local commits are not evidence of remote publication.
6. Open the verified acceptance URL and provide usable sign-in instructions. Do not describe a model-disabled environment or a stopped worker as ready for interactive AI acceptance.


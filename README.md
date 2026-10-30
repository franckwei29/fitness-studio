# Fitness Studio

A web application for confirmed member intake, versioned training and nutrition plans, and controlled AI operations. Members review proposed changes before activation. Owners manage service groups, approvals, budgets, incidents, and agent releases.

The interface and application text are English. Members can submit English or Chinese messages; Ordinary assistant replies can follow either language; fixed labels and formal structured plans remain English. Generated advice is general fitness guidance, not medical diagnosis or treatment.

## Run locally

Follow [Running Fitness Studio](docs/RUNNING.md) to configure the private environment and start the Docker services. The default browser entry point is **http://localhost:8088**. Model calls are disabled by default and require server credentials and explicit enablement. Initial budgets are configured automatically: US$20 for the studio and US$10 for each role, group and new membership. Existing saved caps and usage are preserved.

## Member workflow

1. Register and complete intake. Review and confirm the extracted facts; deterministic rules decide admission or identify missing information and exclusions.
2. Request training, nutrition, or a coordinated plan. Candidates remain separate from active plans. Review any required approval, then explicitly activate the candidate.
3. Use **Your fitness assistant** for every question or planning request. Intent is detected automatically. Viewing training and meals keeps the current host; deeper specialist consultation is optional. Questions do not modify plans.
4. Record actual experience, request a cycle review, or select a specific food portion to replace. Revisions are tied to their original input versions and cannot overwrite newer active plans.
5. Read in-app reminders and revisit profile details when circumstances change. A reminder never invents attendance or changes confirmed facts.

The Sage Studio interface includes structured plan cards, business-oriented Activity, and a real rolling AI allowance. See [the interaction and implementation guide](docs/SAGE_STUDIO.md).

## Owner workflow

The owner workspace provides a review queue, operational overview, member groups, membership administration, budget limits, incidents, agent versions, and agent releases. Candidate configuration changes require explicit review and execution. Agent releases require recorded live comparison evidence and explicit owner approval; synthetic test evidence cannot authorize a release.

The overview supports an inclusive UTC date range and a downloadable Markdown operations brief, defaulting to the latest seven days. Both use the same metric service. Candidate creation and successful activation are counted separately. Period costs follow the creation time of model-call records and their current settlement state; current membership and incident totals are explicitly labelled as current state.

If an automatic rollback is blocked by a newer configuration, the affected role is blocked while it still uses the failed version and an incident is recorded. In Agent releases, propose recovery against the latest group version. Review the proposal in Review queue and execute it in Member groups. Only the affected role's retained version changes; other current settings remain intact. Configuration restoration alone does not close the incident: a subsequent successful request under the restored role is required for recovery verification.

## Architecture

```mermaid
flowchart LR
  Browser[React browser client] --> Web[Nginx same-origin proxy]
  Web --> API[FastAPI API]
  API --> SQL[(MySQL: accounts, requests, plans, approvals, ledger)]
  Worker[Durable task worker] --> SQL
  Worker --> Graph[LangGraph role workflow]
  Graph --> Redis[(Redis: checkpoints and dispatch limits)]
  Graph --> Gateway[Policy and budget gateway]
  Gateway --> Gemini[Gemini API]
  Gateway --> OpenAI[OpenAI Responses API]
  Scheduler[Reminder and canary scheduler] --> SQL
```

- The API authenticates users and persists requests before execution. Session cookies and CSRF protection are used on one origin.
- Conversation progress uses authenticated SSE replay in bounded batches. Native browser reconnection resumes with `Last-Event-ID`; duplicate events are ignored and trigger a fresh server snapshot rather than replaying old status onto the interface. A 15-second refresh provides a fallback, and stale overlapping responses cannot overwrite newer snapshots. Task progress identifies the executing specialist without replacing the foreground conversation host.
- Workers check current permissions, input versions, task leases, and immutable agent versions. The model proposes structured outputs; application services validate and commit changes.
- Owners can restrict each group's role-specific profile fields in Member groups. The editor reads the server's fixed role boundaries, distinguishes standard access from an explicit empty whitelist, and shows permission differences. A proposal does not change access until its exact candidate is approved and executed.
- Training and nutrition candidates form an exact validated pair. Activation atomically updates current pointers. Historical versions remain available.
- Members can inspect their immutable confirmed profile history, including the source assessment reference and field changes between versions. History reads are scoped to the authenticated member and never restore older facts or activate plans.
- Every paid dispatch needs a budget reservation and a shared rate permit: persistent studio-wide hourly limits per provider/model (initially 100) and inherited per-account rolling 24-hour allowances (initially 200). Owners can edit these in **AI usage & limits** or preview and confirm a change in the unified conversation. Member overrides beat group defaults; owners and pre-membership accounts have separate rules. Changes retain existing usage. See [call-limit controls](docs/CALL_LIMITS.md). Unknown provider usage retains funds and is not reported as zero cost. Releasing an expired local concurrency slot does not reconcile billing or retry the original request.
- Owner trace inspection and JSON downloads use an explicit operational-field allowlist. Raw prompts, health facts, model outputs, provider identifiers, and free-text diagnostics are excluded. Raw LangGraph cloud tracing is disabled even when ambient tracing is enabled. Optional LangSmith export sends only the reviewed snapshot, with an exact fingerprint and a persistent single-attempt receipt; remote uncertainty never changes application work or accounting. See [trace export configuration](docs/RUNNING.md#optional-redacted-trace-export).
- Structurally valid candidates that fail domain validation receive at most two corrective generations shared across the original request's roles. Each correction receives the failed candidate and recorded violations, uses the original deadline and budget scopes, and preserves invalid versions in history. Exhausting the revision or call limit stops the request without activation.
- The scheduler closes expired queued and waiting requests, expires pending approvals, recovers abandoned leases conservatively, and handles deterministic notifications and approved canary observation rules. Expiry preserves completed work and accounting evidence, including reservations for unknown provider usage. Missing observations cannot count as a successful release.
- Users can cancel their pending or running requests from a conversation or Activity, including cycle reviews awaiting confirmation. Cancellation fences task execution and blocks subsequent candidate activation. Already dispatched calls retain their accounting lifecycle; cancellation does not promise a provider refund or erase history. Completed requests cannot be cancelled.

## Source layout

| Path | Responsibility |
| --- | --- |
| `backend/app` | API, domain services, agent workflows, model transport, worker and scheduler |
| `backend/migrations` | Versioned MySQL schema |
| `backend/tests` | Domain, transport and persistence regression tests |
| `frontend/src` | English member and owner interfaces |
| `frontend/e2e` | Browser interaction tests; mocked cases are labelled |
| `scripts/test_backend.py` | Isolated backend test service startup and execution |
| `docs/RUNNING.md` | Environment, deployment and verification commands |

## Verification and scope boundaries

From an installed development environment, run `.venv/bin/python scripts/test_backend.py`. This starts separate MySQL and Redis test services, migrates the test database, and runs pytest with both providers disabled and no API keys. Test records stay separate from the application database. Browser fixture tests verify interaction behavior; they do not establish live model quality.

Build the frontend with `npm run build` from `frontend`. Browser tests require a running development web server and Chrome; use `npx playwright test` from that directory. Some browser tests exercise the API while others provide explicit synthetic responses.

The exercise and food catalogues are deliberately bounded; unsupported inputs must not be silently replaced with invented entries. The API readiness endpoint checks persistence connectivity and schema revision, not successful model generation. A passing build or synthetic test suite is not a substitute for live end-to-end acceptance. Final deployment, live model acceptance, and release evidence must be verified separately.

Model setup, limits, and rollback: [Model providers](docs/MODEL_PROVIDERS.md).

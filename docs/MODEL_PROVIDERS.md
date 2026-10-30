# Model providers and usage controls

The application supports Google Gemini Interactions and OpenAI Responses through the same authorized model gateway. The initial OpenAI option is `gpt-6-luna`; the retained Gemini option is `gemini-3.5-flash-lite`. The catalog deliberately lists only models with implemented request contracts and verified accounting. Arbitrary model IDs are rejected.

## Server configuration

```dotenv
MODEL_PROVIDER=openai
OPENAI_MODEL=gpt-6-luna
OPENAI_API_KEY=<server secret>
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_API_KEY=<optional server secret>
MODEL_CALLS_ENABLED=true
MODEL_HOURLY_CALL_LIMIT=100
MODEL_ACCOUNT_DAILY_CALL_LIMIT=200
MODEL_CONCURRENCY_LIMIT=1
```

The example environment uses OpenAI. An older environment that omits `MODEL_PROVIDER` retains Gemini for compatibility. Restart API, worker, and scheduler after changing server environment values. If `APP_ENV_FILE` is set, update that actual private file; changing a different `.env` does not change container credentials.

Keep credentials in the server environment. API responses show only whether a provider is configured. Keys are excluded from the frontend, repository, exported traces, and sanitized provider errors. No new SDK is required: the existing HTTPX dependency handles both transports. Requests use fixed official endpoints, disable redirects and storage, and make one HTTP attempt without hidden transport retries.

## Role configuration

Open **Agent versions**. Choose Provider, Model, Agent role, Version label, Response limit, and (for Luna) Reasoning effort. Save the candidate, evaluate it against the current baseline, inspect the recorded results, explicitly confirm publication, and release through Canary before expansion. Saving alone neither calls a provider nor changes live member groups.

The model catalog is shared by server validation and the selector. New candidates explicitly store provider, output limit, and reasoning effort. Their immutable fingerprint covers model, role, prompt, and generation options. A provider/model mismatch is rejected. Provider identity is also recorded on each paid call.

The environment default applies to tasks whose group has no explicit role version. Explicit group versions remain authoritative. Existing tasks retain their pinned version: they never silently rebind after an environment change. Existing permission and group-version checks may stop a task for review if its authorized inputs change.

Migration `d2401030a001` labels historical versions and calls as Gemini. Historical output limits remain nullable to preserve original task-specific payloads and evidence fingerprints. New candidates have an explicit cap. Recorded outputs can still be recovered after a worker crash without a second paid call.

## Quotas

- Every provider/model has one persistent studio-wide rolling 60-minute allowance, initially **100 calls**. Roles sharing that model share that allowance.
- Each member inherits a per-person rolling 24-hour allowance from their group, initially **200 calls**, unless a personal override is set. Owner accounts have separate editable allowances; pre-membership accounts use the explicit initial rule. These daily counts are shared across all models.
- Request-level attempt bounds, concurrency controls, and monetary budgets remain independent.
- Each real outbound attempt counts, including a retry that dispatches again. Viewing saved data, deterministic owner lookups, saving a candidate, and reuse of a settled result do not count.
- Proven undispatched cancellations return their hourly and account permits. Dispatched calls with uncertain outcomes retain usage and money holds for review.

Redis Lua checks are atomic across workers. The legacy global ledger remains for replay detection and migration. Startup runs `app.quota_migration` before workers start, preserving recent account history and seeding provider/model hourly history without duplicating entries. Persistent Redis volumes must be retained during upgrades.

The member Activity page shows the personal allowance and its recovery time. **AI usage & limits** shows editable model limits, member allowances and separate spending controls. Environment values seed missing policies at startup; saved database settings survive restarts and environment changes. See [call-limit controls](CALL_LIMITS.md). These are rolling windows, not midnight resets. Availability is a snapshot rather than a guarantee that every specialist's next request can acquire its role budget.

## Accounting

Internal budgets are cumulative USD allowances and are separate from an OpenAI platform balance. The app does not claim to read the provider balance. All required scopes (global and applicable role/group/member scopes) must have enough available funds.

Luna standard short-context text prices verified 2026-10-30, per million tokens:

| Category | USD |
| --- | ---: |
| Ordinary input | 0.10 |
| Cached input reads | 0.01 |
| Cache writes, 30-minute lifetime | 0.125 |
| Output, including reasoning | 0.50 |

Requests explicitly use Standard service and a 30-minute cache lifetime. Input tokens are partitioned into ordinary, cached-read, and cache-write counts. Reasoning tokens are already part of output tokens and are not added again. Missing, inconsistent, unsupported lifetime, or out-of-bound usage remains uncertain rather than being treated as free. Image, audio, hosted tools, long-context pricing, and 12-hour cache writes are outside this transport's enabled contract.

Reservations use a conservative byte-derived input allowance plus framing overhead, and the configured output cap. Luna reservations cover the cache-write input rate. Gemini retains its full thought-output allowance because its thought accounting differs. A reported cost beyond the request reservation bound requires reconciliation. Unknown usage is never refunded or retried automatically.

## Deployment and recovery

1. Back up the existing database and retain Redis persistence.
2. Configure the desired private environment; do not commit it.
3. Deploy with the existing Compose project and volumes. Initialization runs Alembic, bootstrap, and quota history migration.
4. Verify `/health/ready`, owner model configuration, and member usage.
5. Run a small fictional-data acceptance request; check model/provider, response contract, settled usage, and business validation.
6. To switch an unconfigured runtime default back to Gemini, set `MODEL_PROVIDER=gemini` and restart the app services. To roll back an explicitly released role, use the retained version through the existing reviewed rollback controls. Do not remove additive database columns during normal application rollback.

No automatic provider fallback is enabled. A provider outage does not silently change the selected model or make additional paid attempts.

## Sources

- [OpenAI Luna model](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI prompt caching and usage calculation](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Google Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)

Use **Compare with your current Coach** for a model-only comparison. This materializes the effective group version even before any Coach request and copies its full instructions. Per-result quality ratings are not a release gate. Automatic evidence checks and exact owner approval remain required.

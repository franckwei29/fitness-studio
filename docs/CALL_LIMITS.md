# AI usage & limits

## Rules and operation

All UI text is English. Owners open **AI usage & limits** from the existing sidebar or use the same main Studio assistant conversation. Members, member groups, approvals and other business pages remain normal pages.

| Rule | Scope | Initial value | Override / reset |
| --- | --- | --- | --- |
| Studio model limit | All accounts sharing a provider + model | 100 calls per rolling hour | Independently editable for Luna and Gemini |
| Group allowance | Each member individually | 200 calls per rolling 24 hours | Personal exception takes priority |
| Personal allowance | One existing member | Inherit current group | Restore group default without deleting history |
| Owner allowance | One owner account | 200 calls per rolling 24 hours | Separate from member groups |
| Initial account allowance | Each account before membership | 200 calls per rolling 24 hours | Explicit editable rule; admission does not reset usage |

Every account remains subject to the selected model's studio limit, shared concurrency gate and applicable monetary budgets. These are call counts, not message counts. A plan can use several calls. Local owner lookups and settings changes use no provider calls.

### Studio limits

Each model card shows the current rolling-hour count, configured status, default-model marker, editable hourly limit, 100/200/500 presets, old/new preview and explicit Save changes. Used counts are read-only. On success the old/new limits appear beside the action. Lowering below current usage pauses admission until enough historical calls expire. It never erases counters.

### Member allowances

Actual group names, membership counts and inherited-member counts come from SQL. Choose a group to edit its per-member allowance and inspect its members. Search by name, email or group. Results are paginated in groups of 50. Each member displays the effective allowance and **Group default** or **Custom allowance**. Choose a custom allowance to enter a number; choose **Use group default** and **Restore group default** to resume inheritance. No ID entry is needed.

Member and group detail pages also expose allowance controls. A group change preserves any personal exception and all usage. A member using inheritance immediately follows the new group. The existing profile-context and queued-work safeguards still apply.

### Spending controls

The main spending panel shows cumulative studio limit, spent, reserved and available amounts, explicitly in USD with **No automatic reset**. Advanced controls retain shared group caps, individual member caps and AI assistant role caps. A shared group dollar cap is an additional total spending ceiling; it is not a per-member call allowance. Existing budget accounts, reservations and accounting semantics are unchanged.

## Conversation commands

Use the real group/member names visible in the application:

```text
Set the studio-wide Luna hourly limit to 500 calls.
Set the studio-wide Gemini hourly limit to 200 calls.
Set Default to 200 calls per member per day.
Give Alice 300 calls per day.
Restore Alice's group allowance.
Show members with custom allowances.
Set my owner daily allowance to 250 calls.
Set the initial account allowance to 200 calls per day.
```

A command creates a typed preview only. The card shows the exact object, scope, source, current value and proposed value. **Confirm change** calls the same service as the settings page and appends an audit-backed receipt to the same conversation. Duplicate names produce separate selectable member previews. **Open settings** and **View member** open exact records, retaining the original conversation and draft.

Ambiguous instructions such as `Increase the limit to 500.` ask for the object; they do not guess. The command parser supports the above English patterns and corresponding Chinese quota terms; it is deterministic rather than a guarantee of arbitrary natural-language interpretation. Negative, fractional, multiple or out-of-range numbers require clarification. Unsubmitted applicants use the initial rule and are not exposed as existing member overrides.

## Persistence and concurrency

Migration `e261030b001` adds `call_limits`. It does not rewrite old budgets, calls, Redis history, profiles or audits. Bootstrap inserts missing policies only. `MODEL_HOURLY_CALL_LIMIT` and `MODEL_ACCOUNT_DAILY_CALL_LIMIT` seed first-start model/initial policies. Existing owner/group policies and saved edits survive restarts and environment changes. New groups inherit the persisted initial daily seed when first configured.

Each policy has a monotonic version. A member override can hold `NULL`, meaning inheritance; restoration increments its version instead of deleting the row. The edit fingerprint also includes the current member group and group-policy version. Concurrent or stale edits receive HTTP 409, including a member move between preview and confirmation. A background UI refresh updates untouched values but retains an edited draft and its original evidence.

A short SQL policy lock serializes quota edits, group moves and admission through the Redis gate and dispatch marker. It is released before provider network activity. Redis Lua performs atomic rolling-hour, rolling-day and concurrency admission across workers. Completed/unknown calls retain history; proven undispatched cancellation returns its permits using the existing path. Already dispatched calls continue; later dispatch attempts read current SQL policy.

Only owners can read administrative policies or change them. Every successful change records actor, object, previous/new effective calls, source, version, reason and timestamp. Normal member usage responses expose their effective allowance, source, model limits and recovery times, not other members' usage.

## API

| Endpoint | Behavior |
| --- | --- |
| `GET /api/owner/call-limits` | Model/group/owner/initial policies, rolling counts and searchable paginated member allowances |
| Query `query`, `group_id`, `custom_only`, `offset` | Search actual names/emails/groups, filter group/exceptions, paginate |
| `GET /api/owner/call-limits/entry?scope=...` | Exact current policy, inheritance and edit fingerprint |
| `POST /api/owner/call-limits` | `{scope_key,calls,expected_fingerprint,reason}`; `calls:null` restores member inheritance |
| `GET /api/me/usage` | Current personal effective rule, usage, blocking reason and capacity times |

Limits accept whole numbers from 1 to 1,000,000. Zero is not an implicit unlimited or disabled mode. No monetary ledger is reset by a quota change. Raising an app limit does not raise the provider's own account limits, credit balance or service availability.

## Acceptance coverage

Automated backend checks exercise SQL persistence, environment changes, group inheritance and personal exceptions, restoration/ABA stale edits, name/email search, reassignment without usage reset, reduced and increased limits, provider independence, concurrent edits, authorization, strict input validation, typed chat previews, audit receipts and shared page state. A gateway test uses synthetic provider responses with real SQL/Redis and checks that a saved reduction blocks the next call and a later increase permits it. Existing Redis concurrency tests verify no overshoot.

Browser checks cover desktop/mobile model editing, old/new receipts, member overrides/restoration, stale-edit draft preservation, exact settings navigation, and confirmation inside the single main conversation. Existing business-page workflows are retained. These automated browser cases use API fixtures; local deployed UI/API verification is recorded separately in `CALL_LIMIT_ACCEPTANCE.md`.

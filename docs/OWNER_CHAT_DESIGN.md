# Owner conversation workspace — implemented design

Status: implemented on 24 October 2026. Acceptance evidence is recorded in OWNER_CHAT_ACCEPTANCE.md.

## Workspace contract

Use **Studio assistant** as the primary entry point for owner work. Support natural-language discovery, authenticated business cards, small inline forms, explicit decisions and durable result receipts. Keep existing pages for complex comparisons, configuration and history. A page transition must open the exact selected record, not leave the owner searching a general list.

This extends the existing multi-role architecture. It does not make the Technical Manager model the owner or grant it unrestricted member-profile access. English interface labels remain consistent with Sage Studio; requests may be written in English or Chinese.

## Navigation contract — revised after visual review

- Keep one persistent **Studio assistant** conversation for owner work. Requests about different business areas append to the same message history and use the same composer. Task categories label cards; they never select separate task-specific conversations.
- Keep **Members**, **Review queue**, **Budget limits**, **Agent releases** and the other existing destinations as ordinary lists, forms and detail pages. They do not become chat interfaces.
- Inline approval, budget and group-assignment cards expand within their original message. A completed action updates its card and appends a result receipt without replacing previous messages.
- Complex actions open the exact business record. **Back to conversation** preserves message history, composer draft, form drafts, action state and the previous reading position.
- Sidebar navigation and conversation shortcuts are two entry points to the same records. A change made on a business page updates the corresponding card and receipt in the original conversation.
- The application now connects these interactions to authenticated service APIs. The earlier interactive mockup remains a visual reference only.

## Problems addressed

- `conversation_runtime.py` previously supplied the ordinary operations conversation with request-state counts, global budget amounts and unknown-call counts. Selected incident analysis has an additional operational context path. Local owner workspace lookups now resolve concrete records; explicit AI analysis retains its separate operational context.
- `ConversationView.tsx` now also renders owner business cards, inline decisions and audit-backed receipts.
- Hash-addressed owner routes now select exact records after login and refresh. Existing list and form pages remain intact.
- Conversation cards identify authorized records by readable member, group or version labels. Full review evidence stays in the authenticated local surface.
- `AwaitingConfirmation` is a request state, not a count of owner approvals. It can refer to a member decision. Owner approvals must come from approval records and the relevant business lifecycle.
- `policy.py` gives TechnicalManager no member-profile fields. An authenticated owner viewing a card locally is different from sending that card's health contents to Gemini.

## Existing owner-facing capability inventory and conversational access

The inventory distinguishes existing business controls and their conversational entry points. Complex configuration, quality review, release and recovery forms remain on focused detail pages.

| Area and source | Existing manual capabilities | Conversational access | Detail surface |
|---|---|---|---|
| Studio overview — `Dashboard.tsx` | Refresh studio/member/request counts; inspect budgets; select a recent request and inspect tasks, calls and events | Read-only metric and request cards; explain business status and required next step | Open the exact request trace when detailed evidence is needed |
| Reporting — `OperationPeriod.tsx` | Select UTC reporting dates; view counts and role/member cost breakdowns; download Markdown operations brief | Show a live summary and open the reporting section to choose UTC dates and download the operations brief | Full tables for longer comparisons |
| Review queue — `Approvals.tsx` | Inspect intake, training, nutrition, paired-plan, configuration and release approvals; approve, request revision or reject, with notes | Find eligible pending items, expand authenticated review evidence, collect notes, and submit an explicit decision | Full plan/configuration comparison for lengthy evidence; same record and version |
| Members — `Members.tsx` | View member status/group/context; change a member's group with a reason | Resolve the member, show current and proposed group and downstream effects, then confirm reassignment | Exact member details for investigation; not a new health-editing tool |
| Member groups — `Groups.tsx` | Edit service capabilities, plan-review requirement and profile-field access; propose a configuration change; execute an approved change | Open the exact group editor to draft a change; inspect before/after evidence and apply an eligible approved change in chat | Full configuration editor for multiple changes |
| Field permissions — `FieldPermissions.tsx` | Restrict role-specific profile fields within fixed role limits | Open the exact group configuration and inspect the affected fields in its editor and review | Never broaden the fixed Technical Manager profile boundary |
| Budget limits — `Budgets.tsx` | Inspect global/role/group/member limits, spend and reservations; change a limit with a reason | Present readable scope names and USD; preview old/new limit and require confirmation | Full budget breakdown if requested |
| Uncertain provider costs — `UnknownUsage.tsx` | Inspect unknown usage; release an eligible stopped local execution slot while retaining reserved funds | Explain the blockage and, when server-eligible, offer an explicitly scoped release action | Exact usage record; never label slot release as a refund or settled bill |
| Incidents — `Incidents.tsx` | Inspect evidence; request AI analysis; choose recovery prerequisites; request recovery; verify recovery; close unresolved with notes | Open the exact incident with its prerequisites and next actions; explicit AI analysis continues in the same main conversation | Recovery checklist for several dependent configuration/release steps |
| Agent candidates — `AgentVersions.tsx` | Save role/model/instruction candidates; inspect saved versions | Open candidate creation or an exact saved version, then save through its full instruction editor | Full instruction editor for long prompts; saving is not deployment |
| Comparisons — `Evaluations.tsx` | Choose a baseline, prepare comparison, inspect results, explicitly start live comparison and refresh progress | Open the exact version or comparison; its existing controls prepare, preview call requirements and explicitly start work | Side-by-side response review; preparation itself makes no model call |
| Quality review — `EvaluationReview.tsx` | Rate every result for explanation quality and factual/role compliance; submit notes | Link to the specific comparison and its per-result human quality review | Keep per-result human ratings in a comparison panel; do not invent ratings from a general chat approval |
| Releases — `Releases.tsx` | Propose canary/expansion with reviewed live evidence and observation settings; execute an approved release; inspect observation; restore retained version | Open the exact release with its prerequisites, scope and separate approval, execution and rollback controls | Exact release detail for evidence, thresholds and observation history |
| Blocked rollback — `RollbackRecovery.tsx` | Propose recovery against current configuration; then review and execute through existing flows | One linked recovery checklist retaining the original incident/release context | Exact recovery proposal, approval and execution target |
| Evidence export — `TraceExport.tsx` | Download a redacted JSON trace; export a snapshot to LangSmith if configured | Open the exact redacted trace with local download and separately explicit external-export controls | Detailed redacted snapshot inspection; external export remains separately explicit |
| Personal Activity — `Requests.tsx`, `UsageAllowance.tsx` | Filter own activity; inspect allowance; open a conversation/plan target; cancel eligible own requests | Show relevant ongoing work and an explicit cancellation action | Do not infer permission to cancel all members' requests from owner status |
| Updates — `Notifications.tsx` | Read notices, mark read and follow supported existing links | Render role-appropriate notices and actions | Current generic notices are not an owner review inbox; add owner-specific projection rather than reusing member links blindly |
| Conversation controls — `main.tsx` | Create/reopen conversations, select operations configuration where available, sign out | Keep ordinary navigation/session controls; show selected configuration clearly | Changing the chat configuration selection is not publishing a release |

No new appointment booking, payment processing, billing reconciliation, arbitrary member editing or account deletion is implied by this inventory.

## Owner experience

### 1. Ask what needs attention

Input: “What needs my attention?” or its Chinese equivalent.

Return authenticated records grouped by responsibility:

- **Needs your review**: current unexpired owner approvals, with subject, applicant/group, reason for review, submission time and the exact next action.
- **Ready to apply**: approved configuration or release proposals which have not been executed and whose execution conditions remain valid.
- **Needs attention**: unresolved incidents, blocked recovery steps, incomplete review prerequisites and actionable uncertain usage.
- **Waiting for a member**: requests awaiting that member's confirmation; informational, with no owner confirmation button.
- **In progress**: active work; do not imply that normal execution needs an owner decision.

Counts and lists must use the same filters and time definition. Empty results say there are no matching items, rather than filling the response with unrelated task totals. Expired/superseded approvals are not actionable pending reviews.

### 2. Inspect and decide without searching another page

Example card, illustrative only:

```
Membership application · Demo Member
Needs your review
Submitted 10 minutes ago

[Review application]
```

Selecting the action expands the exact authenticated application in the chat workspace or an adjacent drawer. It shows the relevant submitted facts, version and review reason, with decision controls. Health facts come directly from the authorized application API; they are not automatically included in the model prompt.

```
[Approve]  [Request changes]  [Decline]
```

The final labelled action constitutes an explicit decision when the exact item and evidence are already visible. Do not add repetitive confirmation dialogs to every click. A free-text instruction such as “approve this one” must first resolve one current item and present a decision preview if that context has not already been reviewed. Never treat “looks fine” or a generic agreement as approval of an unidentified item.

After the server confirms the decision, replace action controls with a result receipt. Synchronize the original card, detail page, owner queue and member view. In a concurrent decision or stale-version conflict, display the latest result and ask the owner to review current evidence; do not silently execute against a new version.

### 3. Complete a small edit in chat

Example: “Move Demo Member to the beginner group.”

Resolve identity using authorized member records. If several records match, show a choice. Display current group, target group, reason field and implications for queued work/plan context. **Confirm group change** calls the existing member-assignment service and produces a receipt. The model cannot invent a member ID or an available group.

Budget changes follow the same pattern: named scope, old/new USD limit, reason and one explicit confirmation. A suggestion is a draft, not a saved setting. Existing hourly/daily call caps remain distinct from monetary budget limits.

### 4. Continue complex work in a focused panel

Example: “Compare the new coach instructions and release them if appropriate.”

The focused pages guide the sequence: prepare evidence → start a budgeted live comparison → human quality review → release proposal → owner approval → execution → observation. Reuse the existing required gates; a broad chat request does not mark them complete. Direct the owner to the exact comparison or release when detailed review is necessary, retain chat context, and return a status receipt to the conversation after each completed step.

Chat remains a single Studio assistant persona. Internal domain handlers do not need visible role switching on every administrative action.

## Interaction and presentation rules

- Use Sage Studio typography, outline icons, compact cards and clear status labels. Cards may expand into a drawer before full-page navigation.
- Buttons say what they do: **Review application**, **View member confirmation**, **Apply approved change**, **Open comparison**, **View recovery steps**.
- Show **Loading your review queue…**, **Saving decision…**, and final receipts from real request state. Do not simulate thinking or progress percentages for a database lookup.
- Display USD amounts, with a small-cost tooltip or sufficient precision where useful. Never expose `micros` as the ordinary business unit. Reserved funds are not settled spend.
- Historical messages retain their original text; actionable cards fetch current status and show a last-checked time. Old buttons become unavailable once acted upon or superseded.
- Querying status and opening records need no confirmation. Model-spending actions show planned calls and applicable limits before starting. Scope-changing writes show the exact target and before/after preview.
- Large lists are searchable and paginated; chat shows a concise initial set with **Show more**. Do not send the entire queue or trace history to Gemini.

## Implementation outline

1. **Owner attention projection.** The owner-authenticated `/api/owner/workspace` endpoint returns typed items, readable subjects, responsibility, business state, version/fingerprint, supported actions and exact navigation targets. Derive these from Approval, ChangeRequest/release, Incident and authorized activity services. Enforce permissions on every read and action.
2. **Durable record navigation.** Introduce URL-addressable owner detail routes (for example `/owner/approvals/:id`, `/owner/incidents/:id`, `/owner/releases/:id`) using hash routes such as `#/owner/approvals/:id?conversation=:conversationId`. Load the specific record after authentication, handle unavailable/expired items and provide a return-to-conversation target.
3. **Structured owner chat intents.** Typed cards cover lookup, opening records, action previews and next-step guidance. Route common explicit controls directly without inference. Common English and Chinese requests use local intent matching and real record labels. Ambiguous or unsupported requests show workspace choices; no write is inferred from free text. Explicit incident analysis remains a model-backed action.
4. **Shared action and card components.** Reuse the same review, group-change and budget forms/services in chat and pages. Do not copy business policy into a second chat-only implementation. Create target-bound action tokens or equivalent validated descriptors; never execute arbitrary URLs or commands returned by a model.
5. **Separate model and UI data.** The server can render an authorized owner card without sending its sensitive fields to Google. Keep TechnicalManager's profile-field restrictions. For explanations, supply only allowlisted operational facts; health evidence remains in the local authorized review surface under current policy.
6. **State and receipts.** Persist the originating conversation/item/action reference. Recheck version, fingerprint, permission and current eligibility immediately before execution. Preserve idempotency; prevent double-submit; recover an uncertain response by checking server state. Poll or stream shared status updates and restore pending work after reload.
7. **Complex guided flows.** Comparison, release and recovery cards open exact detail targets. Preserve separate review and execution phases, hard eligibility limits, budgets, cooldowns and verification requirements.

## Implemented integration groups

- **First:** attention query, separation of owner/member responsibility, exact approval-detail navigation, local evidence drawer, inline intake/plan review decisions and synchronized receipts.
- **Second:** reports/downloads, group reassignment, budget preview/confirmation, personal cancellation and incident details.
- **Third:** complex configuration, field permissions, comparison/quality review, release/rollback/recovery and optional external export guidance.

Every inventory area has a conversational entry point. Completing every form inside message bubbles is not necessary; the objective is to remove manual searching while preserving readable evidence and deliberate decisions.

## Acceptance scenarios

1. Chinese and English requests for owner attention return matching real items, not just global state counts.
2. A member-confirmation task is explicitly labelled as waiting for the member and has no owner-approval action.
3. Selecting a review card opens that exact item. Refreshing its detail URL preserves identity; returning to chat preserves the conversation.
4. Two similarly named applicants trigger disambiguation. A model-generated or unauthorized ID never grants access.
5. An owner can inspect and approve a current intake within the workspace; the member updates automatically. No member health facts appear in the outgoing model context.
6. Repeated clicks/retries do not duplicate decisions. Another tab's approval or changed applicant facts invalidate the old action safely.
7. Requesting changes, rejecting, expiry and supersession all have distinct receipts and valid next steps.
8. A budget or group change shows the exact before/after values and changes nothing until confirmation. Invalid or stale changes retain the existing value.
9. Approved configuration is not displayed as applied until execution succeeds; release observation and recovery verification remain separate.
10. Read-only card clicks make zero model calls. Live comparisons state their planned calls and preserve all existing budget and quota enforcement.
11. Unknown provider usage keeps reserved funds after a slot release. Cancelling work does not promise a refund or automatic rollback.
12. Failure, timeout, reload and return from a detail page preserve truthful action state and allow safe continuation.
13. Owner-only deep links reject member access; expired sessions return to the intended record after login without exposing it beforehand.
14. Small screens and keyboard navigation support card expansion, detail review, explicit actions, progress and status announcements.
15. External export names its destination and exact redacted snapshot and cannot occur merely because the owner asks to inspect a trace.

## Source references

Frontend: `main.tsx`, `ConversationView.tsx`, `Approvals.tsx`, `Dashboard.tsx`, `OperationPeriod.tsx`, `Members.tsx`, `Groups.tsx`, `FieldPermissions.tsx`, `Budgets.tsx`, `UnknownUsage.tsx`, `Incidents.tsx`, `AgentVersions.tsx`, `Evaluations.tsx`, `EvaluationReview.tsx`, `Releases.tsx`, `RollbackRecovery.tsx`, `TraceExport.tsx`, `Requests.tsx`, `UsageAllowance.tsx`, `Notifications.tsx`.

Backend grounding: `conversation_runtime.py` (operations context), `policy.py` (role boundaries), `admission.py` (owner review and exact facts), `member_admin.py` (group assignment), `operation_summary.py` (report semantics). Existing endpoint implementations remain authoritative for execution rules; implementation must audit each handler before exposing it through a new action surface.

## Implementation boundaries

- Ordinary owner lookup, evidence inspection and action preview are deterministic local operations and consume no model calls. The UI never treats a general phrase such as “looks fine” as a decision.
- Explicit budget amounts and uniquely named destination groups may prefill a preview. The final labelled button is still required.
- Simple inline controls cover review decisions, member-group changes, budget changes, notification reading, own activity cancellation, eligible uncertain-slot release and applying an eligible approved configuration.
- Candidate creation, field-permission editing, comparison ratings, releases, incident recovery and evidence export use the existing focused forms. All actions remain subject to the original service guards.
- Receipts are generated from authenticated owner audit events and deduplicated by audit ID. Failed writes refresh the current record without creating a success receipt.
- Workspace cards paginate by offset. Card status refreshes every 15 seconds and after a successful action. Approval remarks, chat drafts and inline form drafts survive return navigation; drafts are stored for the current browser session.
- GitHub publication provides source code. The acceptance deployment runs locally at port 8091.

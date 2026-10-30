# Persistent call-limit acceptance

Date: 2026-10-30. Local acceptance environment: `http://127.0.0.1:8091/`.

## Verified results

- Full backend regression: **323 passed** using isolated MySQL and Redis. No external inference in the suite; provider responses are synthetic where dispatch is exercised.
- Full browser regression: **53 passed**. Includes desktop/mobile quota controls, member inheritance and restoration, stale drafts, the single owner conversation and exact detail navigation. Browser regression cases use API fixtures.
- Frontend TypeScript/Vite production build, Ruff and whitespace checks passed.
- Migration `e261030b001` installed on the existing local acceptance database after a private local SQL backup. Existing volumes, monetary accounts, reservations, membership profiles and usage were retained.
- Deployed UI: changing Luna's shared hourly limit **100 → 500** immediately changed the displayed denominator while retaining the used count.
- Deployed main conversation: `Set the studio-wide Luna hourly limit to 100 calls.` produced a **500 → 100** preview, required **Confirm change**, removed that confirmation after success and appended an audit-backed receipt to the same conversation.
- **Open settings** opened the exact Luna policy with conversation context retained. Runtime restart preserved the saved limit and version.
- Deployed API with the existing demo member: a temporary **300 calls/day** override appeared immediately in the member's own usage response; restoring the group default returned it to **200** without changing used calls.
- The deployed stale edit returned **409**; member access to owner settings returned **403**. The live checks did not dispatch paid provider calls.

The local acceptance model limits remain **Luna 100/hour** and **Gemini 100/hour** after verification. Existing group defaults, owner allowance and initial account allowance are **200/day**. The demo member's personal rule was restored to inheritance. No test mutation was made to the separately supplied new-member UAT account.

## Meaningful boundary checks

The new integration tests cover personal override precedence, group impact counts, group changes with/without exceptions, usage retention, restoration without version reuse, environment seed persistence, independent model limits, increasing and lowering live policies, concurrent conflicting edits, strict numeric input, owner-only access, chat preview/no-write-before-confirmation, real audit receipts and a real gateway path using synthetic responses.

Existing Redis tests cover concurrent call admission without overshoot. The new gateway test verifies that lowering a saved owner allowance blocks the next transport dispatch and raising it permits a later call. The final affected-area follow-up run passed **35 tests**, covering the recovery-time response fields and model-name/ambiguous-window parser refinements.

## Limits of this acceptance

This confirms application-level admission and local behavior. No production traffic or paid-provider load test was performed. Raising the studio limit cannot raise provider rate limits or replenish provider credit. The acceptance URL is local to this Mac; it is not a public deployment.

The server command parser recognizes the documented quota patterns. Ambiguous targets or incompatible hourly/daily instructions require clarification; unsupported phrasing can always use exact settings links.

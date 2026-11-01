# Conversational model comparison

The owner can ask “比较 Canary 当前的 Coach 和 Gemini” or “Compare Canary's current Coach with Gemini using the same instructions and test cases.” The assistant reads the current configuration and supported models, then presents a proposal. Missing group, role, or target information is clarified in chat. Follow-ups retain the most recent comparison context even after a proposal is superseded or dismissed; changing back to a supported target prepares a new preview. One **Confirm and run** starts the comparison; progress and recorded outputs remain in the conversation.

## Execution and validation

- `comparison.run_current` requires `group_id`, `role`, and `model`. Unique provider aliases resolve to configured model IDs. New chat proposals cannot use the old prepare-only comparison tools. The standalone version/evaluation APIs remain unchanged.
- Preview resolution does not create agent versions or evaluation runs. It captures the group version, effective instruction/version fingerprint, candidate fingerprint, and test-suite fingerprint. The task confirmation fingerprint includes that snapshot.
- Proposal generation checks real record types, role compatibility, supported models, credentials, and same-model requests. Invalid actions are fed back into the existing four-turn reasoning budget. Invalid historical cards display **Update needed**, hide confirmation, and offer to prepare a corrected proposal in chat.
- Confirmation locks and rechecks the snapshot, materializes/reuses the effective baseline and matching candidate, prepares the evaluation, and enqueues its work in the existing atomic task transaction. Repeated confirmation returns the original receipt. A changed snapshot requires a new preview. A semantic comparison-revision flag lets the backend retire the prior preview from stored conversation context, including revisions to invalid targets, without requiring the model to reproduce its request ID.
- Live comparison cards refresh automatically until a terminal state. Failed checks are shown as results, not silently converted into success. The comparison does not publish a model or alter the group's configuration.
- Owner-chat malformed provider output can retry within the four-turn bound only when its provider usage is complete, priced, within its reservation, and settled. Truly unknown usage still pauses without an invented charge or automatic retry.

## Verification boundaries

Backend SQL tests cover first-use version materialization, candidate reuse, explicit group versions, invalid IDs, cross-role versions, stale snapshots/suites, missing credentials, ambiguous aliases, atomic rollback, cancellation, repeated confirmation, generated-action repair, bounded malformed-output recovery, and unknown-usage holds. Browser fixtures cover inline progress/results, reload, and repair of historical proposals. These tests do not establish live provider behavior or model quality.

Live browser validation uses normal login, composer input, and the single confirmation button. Provider-backed comparisons and wording checks are recorded separately in the local validation report. No candidate is manually selected, and no test budget or call limit is increased.

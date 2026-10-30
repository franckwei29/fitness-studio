# Model trial workflow update — 2026-10-31

Implemented:
- Compare the effective group Coach with a selected model using identical complete instructions; save the current runtime version if no Coach request has run yet.
- Display per-model defined-check results, recorded generation time, and settled average cost. Unknown/incomplete cost is not zero. Paired examples are expandable.
- Remove quality ratings as a publication prerequisite. Preserve live evidence reconstruction, automatic rules, exact-version approval and owner authorization.
- Confirm and publish an exact proposal in one transaction; no approval means no execution.
- Select multiple existing members, inspect their source/destination groups, then confirm an atomic transfer. Conflicts roll the whole batch back; personal allowances and usage are retained.
- Find trial requests by exact release group, group version and agent version; open execution evidence in the conversation panel.

Validation:
- 53 relevant backend regression tests passed before the final trial-request projection refinement. The modified release/observation/handoff regression was rerun and passed after that refinement.
- 9 distinct browser scenarios passed across targeted runs: comparison completion/incompletion, multi-member confirmation, fresh workshop preparation, publication confirmation, rollback reason, and three existing conversation preservation/confirmation scenarios. Browser evidence uses fixtures, not live provider quality results.
- Frontend production build and changed backend lint checks passed.
- Local deployment at http://127.0.0.1:8091/ updated; readiness and served production bundle verified.
- Authenticated local smoke prepared a real Luna/Gemini comparison with identical instructions, zero evaluation calls and unchanged group configuration. Record: `.local/model-trial-smoke.json`.

Not claimed: a new completed live provider benchmark, a live model-quality winner, or a completed rehearsal of every English demo prompt. The prepared comparison must be started with configured provider credentials and sufficient existing budgets before presentation.

Latest combined demo guide (Chinese instructions, English inputs):
- [Five-person demo and operation guide](Presentation_Demo_五人串讲与操作手册_最新版.md)

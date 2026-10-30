# Training-first revision acceptance

A member asking to revise training with existing meals immediately gets a Coach draft. There is no up-front decision about whether meals should change. Current plans stay active.

Nutrition then reviews the exact training draft and current meals. Its typed review either retains the existing meals or proposes concrete changes with an explanation. Retention copies the saved meal content exactly, ignoring any accidental alterations in the model's proposal. A new linked nutrition version records the new training association without changing meal content. Catalogue/dietary validation and existing studio approval requirements still apply. This is a general-plan AI review, not clinical verification of energy adequacy.

When meal changes are suggested, the conversation provides:
- Review training and suggested meal changes: open the exact pair draft with changes and final confirmation.
- Keep meals · Revise training: withdraw the old candidate request and ask Coach to adapt training to the original meals, retaining the original goal and deadline. Nutrition checks the new draft again; unresolved conflicts remain visible instead of silently accepting a changed diet.
- Cancel update: retain the current routine.

When meals are retained, the member reviews the training changes and confirms the compatible pair. Activation is atomic, checks current baseline/profile/configuration/approval, and completes the request. Neither draft becomes active during generation or review.

## Verification

Automated integration tests use real isolated SQL/Redis with synthetic inference (no paid provider calls). They cover draft-first routing, both review outcomes, exact meal retention despite a model's altered portion, unchanged current pointers before confirmation, final activation, no cancellation after activation, scoped rework and replay rejection, and inability to activate the withdrawn draft. Browser tests cover conditional choices and exact draft navigation. Existing combined, nutrition validation, feedback and activation regression suites are also run.

Old completed conversations retain their historical replies. Submit a new training revision to exercise this flow; existing plans are not regenerated automatically.

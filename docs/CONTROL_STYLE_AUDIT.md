# Shared control styling audit — 31 October 2026

The application now imports `src/controls.css` after its existing page styles. Primary actions have a filled green treatment; secondary actions have a visible surface and border; destructive actions use a red treatment. Controls include focus, hover, disabled and reduced-motion states. Existing navigation and selectable records retain their distinct layouts.

Adjacent action sequences across 18 components use wrapping action groups with 12px desktop and 10px mobile gaps. This includes evaluations, chat cards, member assignment, approval, budget, request, workflow, trace export and unsaved-change dialogs. Comparison decisions stack on narrow screens. Notifications keep a compact dismissal control. Select menus use the shared surface and boundary styles.

## Verification

- TypeScript and production build passed.
- Browser suite: 75 passed, one live registration test failed before entering the member workspace. The development proxy health endpoint returned HTTP 500; this registration path remains unverified in that environment.
- Six dedicated comparison layout tests passed: 320, 390 and 1440px, light and dark modes. They check spacing, visible boundaries, minimum height, focus and horizontal overflow.
- Read-only inspection of the deployed owner application covered 11 routes at 390 and 1440px: chat, dashboard, approvals, members, groups, budgets, versions, releases, incidents, requests and notifications. All 22 visits had no document horizontal overflow or JavaScript page errors.
- Desktop/mobile comparison screenshots and sampled live pages were visually inspected. Browser regression tests also cover member plans, intake, conversations and dialogs with fixtures; not every possible live data state has been visually reviewed.

No model choice, release decision, member assignment or database reset was performed by this styling audit.

## Follow-up: nested card layout correction

The earlier review missed a rendered intake card. The chat page used the descendant selector `.conversation header`, so its large padding and space-between alignment leaked into profile cards, member action cards and owner task headers. This selector is now scoped to `.conversation .chat-header`. Shared table cells no longer force unbroken lines, and table wrappers constrain overflow.

The profile review now places its icon beside its heading, uses readable field labels, omits redundant previous-value columns for first-time intake, and separates consent from the action footer. At narrow card widths, values and actions stack. Existing saved profiles retain previous/current comparisons. Confirmation logic is unchanged.

New browser geometry checks cover first-time and existing profiles at 320, 390, 768 and 1440px, including title alignment, card containment, consent/action separation and confirmation enablement. These checks address the specific layout failure, rather than relying only on page-level overflow checks.

Follow-up validation completed: 89 owner/member browser tests passed; the live registration test was explicitly excluded because its development API was unavailable in the previous audit. After visual inspection exposed an inherited flex-basis causing overly tall stacked buttons, that rule was corrected and all eight profile layout tests passed again, now asserting a button-height upper bound as well. Desktop and mobile profile screenshots were visually inspected. The rebuilt web container was deployed to the existing 8091 demo; no business decisions were executed.

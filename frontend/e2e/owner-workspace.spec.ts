import { test, expect, type Page } from '@playwright/test';

// UI-only fixtures. Real authorization and write fences are tested against MySQL.
async function ownerWorkspace(page: Page) {
  const conversation = { id: 'owner-main', title: 'Studio work', active_role: 'TechnicalManager' };
  const messages: any[] = [
    { id: 'question', sender: 'User', content: 'Keep all my work in this conversation.' },
    { id: 'answer', sender: 'TechnicalManager', content: 'Your studio work stays together here.', cards: [] },
  ];
  const state = { conversationCreates: 0, groupWrites: 0, submissions: [] as any[], groupId: 'default' };
  const groups = [{ id: 'default', name: 'Default', version: 1 }, { id: 'beginner', name: 'Beginner', version: 1 }];
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let data: unknown;
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if (path === '/api/auth/me') data = { user: { id: 'owner', display_name: 'Studio Owner', role: 'Owner' }, csrf_token: 'test' };
    else if (path === '/api/owner/groups') data = groups;
    else if (path === '/api/conversations') {
      if (route.request().method() === 'POST') { state.conversationCreates++; data = conversation; }
      else data = [conversation];
    } else if (path === '/api/conversations/owner-main') data = { conversation, messages, requests: [] };
    else if (path === '/api/conversations/owner-main/messages') {
      state.submissions.push(route.request().postDataJSON());
      messages.push({ id: `user-${messages.length}`, sender: 'User', content: route.request().postDataJSON().content });
      messages.push({ id: `reply-${messages.length}`, sender: 'TechnicalManager', content: 'Your latest studio information is ready.', cards: [] });
      data = { request_id: `request-${messages.length}`, status: 'Completed' };
    } else if(path==='/api/owner/call-limits/entry')data={scope_key:'member:member-one',label:'Alex Example',calls:null,limit:200,group_limit:200,group_name:'Default',version:1,fingerprint:'x'.repeat(64),source:'Group default',applies_to:'This member only'};
    else if (path === '/api/owner/members') data = [
      { id: 'member-one', display_name: 'Alex Example', status: 'Accepted', group_id: state.groupId, profile_version: state.groupWrites + 1 },
      { id: 'member-two', display_name: 'Robin Example', status: 'Accepted', group_id: 'default', profile_version: 1 },
    ];
    else if (path === '/api/owner/members/member-one/group') {
      state.groupWrites++; state.groupId = route.request().postDataJSON().group_id;
      data = { member_id: 'member-one', group_id: state.groupId, status: 'Accepted' };
    } else if (path === '/api/owner/conversations/owner-main/receipts') data = { recorded: false };
    else if (path === '/api/owner/incidents') data = [{ id: 'incident-one', title: 'Coach request needs attention', status: 'Open', outcome: 'Pending', current_fence: 1, task_status: 'Waiting', evidence: { role: 'Coach', reason: 'BudgetRequired', attempts: 1 }, recovery: null }];
    else throw new Error(`Unexpected workspace fixture request: ${path}`);
    await route.fulfill({ json: data });
  });
  return state;
}

test('one conversation survives normal member pages, detail refresh, and draft restoration', async ({ page }) => {
  const state = await ownerWorkspace(page);
  await page.goto('/#/assistant/owner-main');
  await expect(page.getByText('Your studio work stays together here.')).toBeVisible();
  const composer = page.getByPlaceholder('What would you like help with?');
  await composer.fill('Continue with the budget after this.');
  await page.getByRole('complementary').getByRole('button', { name: 'Members', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Members', exact: true })).toBeVisible();
  await expect(composer).toBeHidden();
  await page.getByRole('button', { name: /Alex Example/ }).click();
  await expect(page.getByRole('combobox', { name: 'Service group', exact: true })).toBeVisible();
  await expect(page.locator('.business-surface textarea')).toHaveCount(0);
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await expect(composer).toHaveValue('Continue with the budget after this.');
  await expect(page.getByText('Keep all my work in this conversation.')).toBeVisible();
  await page.goto('/#/owner/members/member-one?conversation=owner-main');
  await expect(page.getByRole('heading', { name: 'Alex Example', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Robin Example', exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Alex Example', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click();
  await expect(composer).toHaveValue('Continue with the budget after this.');
  expect(state.conversationCreates).toBe(0);
});

test('incident analysis appends to the current main conversation', async ({ page }) => {
  const state = await ownerWorkspace(page);
  await page.goto('/#/assistant/owner-main');
  await expect(page.getByText('Your studio work stays together here.')).toBeVisible();
  await page.getByRole('button', { name: 'Incidents', exact: true }).click();
  await page.getByRole('button', { name: /Coach request needs attention/ }).click();
  await page.getByRole('button', { name: 'Analyze incident with AI', exact: true }).click();
  await expect(page.getByText('Your latest studio information is ready.')).toBeVisible();
  await expect(page.getByText('Keep all my work in this conversation.')).toBeVisible();
  expect(state.submissions).toHaveLength(1);
  expect(state.submissions[0]).toMatchObject({ purpose: 'Operations', incident_id: 'incident-one' });
  expect(state.conversationCreates).toBe(0);
});

test('member opening an owner deep link cannot load business records', async ({ page }) => {
  const ownerReads: string[] = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith('/api/owner/')) ownerReads.push(path);
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if (path === '/api/auth/me') await route.fulfill({ json: { user: { id: 'member', display_name: 'Member', role: 'Member' }, csrf_token: 'test' } });
    else if (path === '/api/conversations') await route.fulfill({ json: [] });
    else await route.fulfill({ status: 403, json: { detail: 'Owner access is required.' } });
  });
  await page.goto('/#/owner/approvals/private-record');
  await expect(page.getByRole('heading', { name: 'Owner access required' })).toBeVisible();
  expect(ownerReads).toEqual([]);
});

async function actionableWorkspace(page: Page) {
  const conversation = { id: 'owner-cards', title: 'Studio work', active_role: 'TechnicalManager' };
  const approval: any = { id: 'approval-one', subject_kind: 'IntakeAssessment', subject_id: 'assessment-one', subject_version: 2, fingerprint: 'review-v2', decision: 'Pending', created_at: '2026-10-24T00:00:00', expires_at: '2030-10-24T00:00:00', context: { facts: { age: 28, goal: 'Build a consistent routine', resolved_past_injury: true } } };
  const budget = { id: 'budget-one', scope_key: 'global', limit_micros: 1000000, spent_micros: 1000, reserved_micros: 2000, fingerprint: 'budget-v1' };
  const state = { decisions: [] as any[], budgetWrites: [] as any[], staleBudget: false };
  const messages: any[] = [{ id: 'ask', sender: 'User', content: 'Show reviews and the studio budget.' }, { id: 'reply', sender: 'TechnicalManager', content: 'Here are the exact records for your studio.', cards: [{ type: 'OwnerWorkspace', area: 'attention' }, { type: 'OwnerWorkspace', area: 'budgets' }] }];
  const receipts = new Set<string>();
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname; let data: unknown;
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if (path === '/api/auth/me') data = { user: { id: 'owner', display_name: 'Studio Owner', role: 'Owner' }, csrf_token: 'test' };
    else if (path === '/api/owner/groups') data = [{ id: 'default', name: 'Default', version: 1 }];
    else if (path === '/api/conversations') data = [conversation];
    else if (path === '/api/conversations/owner-cards') data = { conversation, messages, requests: [] };
    else if (path === '/api/owner/workspace') {
      const area = url.searchParams.get('area');
      const items = area === 'attention' ? [
        { id: 'approval-one', category: 'Membership', title: 'Membership application · Alex Example', status: approval.decision === 'Pending' ? 'Needs your review' : approval.decision, description: 'Review the submitted application.', target: { page: 'approvals', id: 'approval-one' }, ...(approval.decision === 'Pending' ? { action: 'approval' } : {}) },
        { id: 'member-waiting', category: 'Member confirmation', title: 'Training and meal update · Robin Example', status: 'Waiting for member', description: 'The member must confirm this update.', target: { page: 'dashboard', id: 'member-waiting', section: 'trace' } },
      ] : [{ id: 'budget-one', category: 'Budget', title: 'Studio budget', status: 'Configured', description: 'Review the studio limit and held funds.', target: { page: 'budgets', id: 'budget-one' }, action: 'budget' }];
      data = { area, title: area === 'attention' ? 'Your attention inbox' : 'Budget limits', summary: 'Saved records; changes require your explicit action.', items, total: items.length, offset: 0, limit: 5, has_more: false, checked_at: '2026-10-24T00:00:00' };
    } else if (path === '/api/owner/approvals') data = [approval];
    else if (path === '/api/owner/approvals/approval-one/decisions') {
      const body = route.request().postDataJSON(); state.decisions.push(body); expect(body.fingerprint).toBe('review-v2'); approval.decision = body.decision; data = approval;
    } else if (path === '/api/owner/budgets') {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON(); state.budgetWrites.push(body);
        if (state.staleBudget) { state.staleBudget = false; budget.limit_micros = 2000000; budget.fingerprint = 'budget-v2'; await route.fulfill({ status: 409, json: { detail: 'The budget changed. Refresh before editing.' } }); return; }
        expect(body.expected_fingerprint).toBe(budget.fingerprint); budget.limit_micros = body.limit_micros; budget.fingerprint = 'budget-v3'; data = budget;
      } else data = [budget];
    } else if (path === '/api/owner/conversations/owner-cards/receipts') {
      const target = route.request().postDataJSON().target;
      const valid = target.page === 'approvals' ? approval.decision !== 'Pending' : budget.fingerprint === 'budget-v3';
      if (!valid) { await route.fulfill({ status: 409, json: { detail: 'No completed action is recorded.' } }); return; }
      if (!receipts.has(target.page)) { receipts.add(target.page); messages.push({ id: `receipt-${target.page}`, sender: 'TechnicalManager', content: target.page === 'approvals' ? 'Review decision recorded: Alex Example. Approved.' : 'Budget limit updated: Studio budget.', cards: [{ type: 'OwnerReceipt', target }] }); }
      data = { status: 'Recorded' };
    } else throw new Error(`Unexpected action fixture request: ${path}`);
    await route.fulfill({ json: data });
  });
  return state;
}

test('owner approves inline while member confirmation has no decision controls', async ({ page }) => {
  const state = await actionableWorkspace(page); await page.goto('/#/assistant/owner-cards');
  const ownerCard = page.locator('[data-record-id="approval-one"]'), memberCard = page.locator('[data-record-id="member-waiting"]');
  await expect(memberCard.getByText('Waiting for member', { exact: true })).toBeVisible();
  await expect(memberCard.getByRole('button', { name: /Approve|Review application|Confirm/ })).toHaveCount(0);
  await ownerCard.getByRole('button', { name: 'Review application' }).click();
  await expect(ownerCard.getByText('Build a consistent routine', { exact: true })).toBeVisible(); expect(state.decisions).toHaveLength(0);
  await ownerCard.getByRole('textbox', { name: 'Decision notes' }).fill('Reviewed the submitted current facts.');
  await ownerCard.getByRole('button', { name: 'Approve', exact: true }).click();
  await expect(page.getByText('Review decision recorded: Alex Example. Approved.')).toBeVisible();
  await expect(ownerCard.getByRole('button', { name: 'Approve', exact: true })).toHaveCount(0); await expect(ownerCard.locator('form,input,textarea')).toHaveCount(0); expect(state.decisions).toHaveLength(1);
  await page.reload(); await expect(page.getByText('Review decision recorded: Alex Example. Approved.')).toBeVisible();
  await expect(ownerCard.getByRole('button', { name: 'Review application' })).toHaveCount(0);
});

test('budget preview survives exact review navigation and changes nothing before confirmation', async ({ page }) => {
  const state = await actionableWorkspace(page); await page.goto('/#/assistant/owner-cards');
  const card = page.locator('[data-record-id="budget-one"]'); await card.getByRole('button', { name: 'Adjust budget' }).click();
  await card.getByRole('spinbutton', { name: 'Total limit (USD)' }).fill('2.5'); await card.getByRole('textbox', { name: 'Reason', exact: true }).fill('Allowance for the next reviewed comparison.');
  await expect(card.locator('.owner-before-after')).toContainText('$1.00'); await expect(card.locator('.owner-before-after')).toContainText('$2.50'); expect(state.budgetWrites).toHaveLength(0);
  await page.getByRole('button', {name:'Close dialog',exact:true}).click();
  await page.locator('[data-record-id="approval-one"]').getByRole('button', { name: 'Open review details' }).click();
  await expect(page).toHaveURL(/#\/assistant\/owner-cards/); await expect(page.getByRole('dialog', { name: 'Studio work panel' })).toBeVisible(); await expect(page.getByRole('heading', { name: 'Intake Assessment' })).toBeVisible();
  await page.getByRole('button', { name: 'Continue conversation' }).click();
  await card.getByRole('button', {name:'Adjust budget'}).click();
  await expect(card.getByRole('spinbutton', { name: 'Total limit (USD)' })).toHaveValue('2.5'); await expect(card.getByRole('textbox', { name: 'Reason', exact: true })).toHaveValue('Allowance for the next reviewed comparison.');
  await card.getByRole('button', { name: 'Confirm budget change' }).click(); await expect(page.getByText('Budget limit updated: Studio budget.')).toBeVisible();
  expect(state.budgetWrites).toHaveLength(1); expect(state.budgetWrites[0]).toMatchObject({ scope_key: 'global', limit_micros: 2500000, expected_fingerprint: 'budget-v1' });
});

test('concurrent budget edit preserves draft without false success', async ({ page }) => {
  const state = await actionableWorkspace(page); state.staleBudget = true; await page.goto('/#/assistant/owner-cards');
  const card = page.locator('[data-record-id="budget-one"]'); await card.getByRole('button', { name: 'Adjust budget' }).click();
  await card.getByRole('spinbutton', { name: 'Total limit (USD)' }).fill('2.5'); await card.getByRole('textbox', { name: 'Reason', exact: true }).fill('Preview an allowance change.');
  await card.getByRole('button', { name: 'Confirm budget change' }).click(); await expect(card.getByRole('alert')).toContainText('The budget changed.');
  await expect(card.locator('.owner-before-after')).toContainText('$2.00'); await expect(card.getByRole('spinbutton', { name: 'Total limit (USD)' })).toHaveValue('2.5');
  await expect(page.getByText('Budget limit updated: Studio budget.')).toHaveCount(0); expect(state.budgetWrites).toHaveLength(1);
});

for (const width of [320, 768, 1440]) test(`owner action cards fit ${width}px within one conversation`, async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message)); await page.setViewportSize({ width, height: 900 }); await actionableWorkspace(page);
  await page.goto('/#/assistant/owner-cards'); await page.locator('[data-record-id="budget-one"]').getByRole('button', { name: 'Adjust budget' }).click();
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveCount(1); await expect(page.getByRole('heading', { name: 'Studio assistant', exact: true })).toBeVisible();
  await expect(page.locator('[data-record-id="budget-one"] svg').first()).toBeVisible(); expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true); expect(errors).toEqual([]);
});

test('large business lists paginate without exceeding the server page limit', async ({ page }) => {
  await actionableWorkspace(page); const offsets: number[] = [];
  await page.route('**/api/owner/workspace?*', async route => {
    const url = new URL(route.request().url()); if (url.searchParams.get('area') !== 'budgets') { await route.fallback(); return; }
    const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 5); offsets.push(offset); expect(limit).toBeLessThanOrEqual(50);
    const items = Array.from({ length: Math.min(limit, 60 - offset) }, (_, i) => ({ id: `budget-${offset + i}`, category: 'Budget', title: `Studio budget ${offset + i}`, status: 'Configured', description: 'An authorized saved budget.', target: { page: 'budgets', id: `budget-${offset + i}` } }));
    await route.fulfill({ json: { area: 'budgets', title: 'Budget limits', summary: 'Saved budgets.', items, total: 60, offset, limit, has_more: offset + limit < 60, checked_at: '2026-10-24T00:00:00' } });
  });
  await page.goto('/#/assistant/owner-cards'); const budgets = page.getByRole('region', { name: 'Budget limits', exact: true });
  for (let offset = 0; offset < 55; offset += 5) { await expect(budgets.getByRole('heading', { name: `Studio budget ${offset}`, exact: true })).toBeVisible(); await budgets.getByRole('button', { name: 'Show more (60 total)' }).click(); }
  await expect(budgets.getByRole('heading', { name: 'Studio budget 55', exact: true })).toBeVisible();
  await expect(budgets.getByRole('button', { name: 'Show more (60 total)' })).toHaveCount(0);
  await budgets.getByRole('button', { name: 'Previous records' }).click(); await expect(budgets.getByRole('heading', { name: 'Studio budget 50', exact: true })).toBeVisible();
  expect(offsets).toContain(55); await expect(page.getByRole('alert')).toHaveCount(0);
});

test('sign-in resumes the exact owner detail URL without exposing it beforehand', async ({ page }) => {
  await ownerWorkspace(page);
  await page.route('**/api/auth/me', route => route.fulfill({ status: 401, json: { detail: 'Please sign in to continue.' } }));
  await page.route('**/api/auth/login', route => route.fulfill({ json: { user: { id: 'owner', display_name: 'Studio Owner', role: 'Owner' }, csrf_token: 'test' } }));
  await page.goto('/#/owner/members/member-one?conversation=owner-main');
  await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible(); await expect(page.getByText('Alex Example', { exact: true })).toHaveCount(0);
  await page.getByLabel('Email address').fill('owner@example.test'); await page.getByLabel('Password', { exact: true }).fill('Fixture-password-123!'); await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Alex Example', exact: true })).toBeVisible(); await expect(page).toHaveURL(/#\/owner\/members\/member-one\?conversation=owner-main/);
  await page.getByRole('button', { name: 'Back to conversation', exact: true }).click(); await expect(page.getByText('Keep all my work in this conversation.')).toBeVisible();
});

test('slow initial history cannot replace a newly created conversation or its draft', async ({ page }) => {
  let releaseList!: () => void, releaseCreate!: () => void, listRequested = false, creationRequested = false;
  const listGate = new Promise<void>(resolve => { releaseList = resolve; }), createGate = new Promise<void>(resolve => { releaseCreate = resolve; });
  const older = { id: 'older-chat', title: 'Earlier studio work', active_role: 'TechnicalManager' }, fresh = { id: 'fresh-chat', title: 'New studio work', active_role: 'TechnicalManager' };
  const messages: any[] = [], submitted: string[] = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname; let data: unknown;
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if (path === '/api/auth/me') data = { user: { id: 'owner', display_name: 'Studio Owner', role: 'Owner' }, csrf_token: 'test' };
    else if (path === '/api/owner/groups') data = [];
    else if (path === '/api/conversations') {
      if (route.request().method() === 'POST') { creationRequested = true; await createGate; data = fresh; }
      else { listRequested = true; await listGate; data = [older]; }
    } else if (path === '/api/conversations/fresh-chat') data = { conversation: fresh, messages, requests: [] };
    else if (path === '/api/conversations/older-chat') data = { conversation: older, messages: [{ id: 'old-message', sender: 'TechnicalManager', content: 'This belongs to the older chat.' }], requests: [] };
    else if (path === '/api/conversations/fresh-chat/messages') { const content = route.request().postDataJSON().content; submitted.push(content); messages.push({ id: 'fresh-message', sender: 'User', content }); data = { request_id: 'fresh-request', status: 'Completed' }; }
    else throw new Error(`Unexpected race fixture request: ${path}`);
    await route.fulfill({ json: data });
  });
  await page.goto('/'); await expect.poll(() => listRequested).toBe(true);
  await page.getByRole('button', { name: 'New conversation', exact: true }).click(); await expect.poll(() => creationRequested).toBe(true);
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeDisabled(); await expect(page.getByRole('button', { name: 'New conversation', exact: true })).toBeDisabled();
  releaseCreate(); await expect(page).toHaveURL(/#\/assistant\/fresh-chat/);
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Keep this message in my new conversation.');
  const lateResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/conversations' && response.request().method() === 'GET'); releaseList(); await lateResponse;
  await expect(page.getByRole('button', { name: 'Earlier studio work', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Keep this message in my new conversation.');
  await expect(page.getByRole('button', { name: 'New studio work', exact: true })).toBeVisible(); await expect(page.getByText('This belongs to the older chat.')).toHaveCount(0);
  await page.getByRole('button', { name: 'Send message', exact: true }).click(); await expect(page.getByText('Keep this message in my new conversation.', { exact: true })).toBeVisible(); expect(submitted).toEqual(['Keep this message in my new conversation.']);
});

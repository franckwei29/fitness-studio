import { test, expect } from '@playwright/test';

test('owner explicitly starts analysis for the selected incident', async ({ page }) => {
  const submissions: Record<string, unknown>[] = [];
  const conversation = { id: 'analysis-chat', title: 'Incident analysis', active_role: 'TechnicalManager' };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown;
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if (path === '/api/auth/me') data = { user: { id: 'owner', display_name: 'Owner', role: 'Owner' }, csrf_token: 'test' };
    else if (path === '/api/owner/groups') data = [];
    else if (path === '/api/conversations') data = route.request().method() === 'POST' ? conversation : submissions.length ? [conversation] : [];
    else if (path === '/api/owner/incidents') data = [{ id: 'incident-one', title: 'Coach request needs attention', status: 'Open', outcome: 'Pending', current_fence: 1, task_status: 'Waiting', evidence: { role: 'Coach', reason: 'BudgetRequired', attempts: 1 }, recovery: null }];
    else if (path === '/api/conversations/analysis-chat/messages') { submissions.push(route.request().postDataJSON()); data = { request_id: 'analysis-request' }; }
    else if (path === '/api/conversations/analysis-chat') data = { conversation, messages: [{ id: 'reply', sender: 'TechnicalManager', content: 'The recorded task is waiting for budget.' }], requests: [] };
    else throw new Error(`Unexpected test request: ${path}`);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Incidents', exact: true }).click();
  await page.getByRole('button', { name: /Coach request needs attention/ }).click();
  await expect(page.getByText(/It uses the configured model budget/)).toBeVisible();
  expect(submissions).toHaveLength(0);
  await page.getByRole('button', { name: 'Analyze incident with AI' }).click();
  await expect(page.getByText('The recorded task is waiting for budget.')).toBeVisible();
  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({ purpose: 'Operations', incident_id: 'incident-one' });
});

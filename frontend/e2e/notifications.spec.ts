import { test, expect } from '@playwright/test';

// UI fixture verifies navigation; reminder timing and deduplication use database tests.
test('continuity reminder leads to plans without treating a read as feedback', async ({ page }) => {
  const writes: string[] = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET') writes.push(path);
    let data: unknown;
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if (path === '/api/auth/me') data = { user: { id: 'member', display_name: 'Test Member', role: 'Member' }, csrf_token: 'test' };
    else if (path === '/api/conversations') data = [];
    else if (path === '/api/me/notifications') data = [{ id: 'reminder', title: 'How is your routine going?', body: 'Record your experience when you are ready.', kind: 'Continuity', read_at: null, created_at: '2026-10-20T12:00:00' }];
    else if (path === '/api/me/plans') data = { member_status: 'Accepted', catalogue_labels: {}, substitutions_enabled: false, current: { version: 0, training_id: null, nutrition_id: null, pair_id: null }, plans: [], pairs: [] };
    else throw new Error(`Unexpected request: ${path}`);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Updates', exact: true }).click();
  await page.getByRole('button', { name: 'Record training feedback', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Your plans', exact: true })).toBeVisible();
  expect(writes).toEqual([]);
});

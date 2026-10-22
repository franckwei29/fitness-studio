import { test, expect } from '@playwright/test';

// UI fixture only: backend integration tests reconcile the corresponding ledger totals.
test('owner filters the period and downloads a matching Markdown brief', async ({ page }) => {
  const queries: string[] = [];
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    let data: unknown;
    if (url.pathname === '/api/auth/me') data = { user: { id: 'owner', display_name: 'Studio Owner', role: 'Owner' }, csrf_token: 'test' };
    else if (['/api/conversations', '/api/owner/groups'].includes(url.pathname)) data = [];
    else if (url.pathname === '/api/owner/dashboard') {
      queries.push(url.search);
      data = {
        members: {}, requests: {}, pending_approvals: 0, unreconciled_calls: 0, budgets: [], recent_requests: [],
        period: {
          start: url.searchParams.get('start') || '2026-10-01', end: url.searchParams.get('end') || '2026-10-07',
          generated_at: '2026-10-07T23:00:00Z', new_members: 2, plan_versions_created: 4, successful_activations: 1,
          active_members_now: 2, unresolved_incidents_now: 0, settled_micros: 27, held_micros: 100, unknown_calls: 1,
          cost_by_role: [{ role: 'Coach', calls: 2, settled_micros: 27, held_micros: 100, unknown_calls: 1 }],
          cost_by_member: [], definitions: ['Candidates are not deliveries.'],
        },
      };
    } else if (url.pathname === '/api/owner/operations-brief') {
      expect(url.searchParams.get('start')).toBe('2026-09-01');
      expect(url.searchParams.get('end')).toBe('2026-09-07');
      await route.fulfill({ contentType: 'text/markdown', headers: { 'Content-Disposition': 'attachment; filename="operations-2026-09-01-2026-09-07.md"' }, body: '# Operations brief\n' });
      return;
    } else throw new Error(`Unexpected test request: ${url.pathname}`);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Studio overview' }).click();
  await expect(page.getByText('Successful activations', { exact: true })).toBeVisible();
  await page.getByLabel('Period start (UTC)').fill('2026-09-01');
  await page.getByLabel('Period end (UTC)').fill('2026-09-07');
  await page.getByRole('button', { name: 'Apply period' }).click();
  await expect(page.getByText('Showing 2026-09-01 through 2026-09-07 (UTC). The default is the latest seven days.')).toBeVisible();
  expect(queries).toContain('?start=2026-09-01&end=2026-09-07');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download operations brief (.md)' }).click();
  expect((await download).suggestedFilename()).toBe('operations-2026-09-01-2026-09-07.md');
});

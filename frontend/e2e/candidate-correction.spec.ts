import { test, expect } from '@playwright/test';

// Presentation fixture; backend tests cover the durable correction workflow.
test('member sees a bounded correction rather than a completed plan', async ({ page }) => {
  const conversation = { id: 'conversation', title: 'Plan correction', active_role: 'Coach' };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown;
    if (path === '/api/auth/me') data = { user: { id: 'member', display_name: 'Demo Member', role: 'Member' }, csrf_token: 'test' };
    else if (path === '/api/conversations') data = [conversation];
    else if (path === '/api/conversations/conversation') data = {
      conversation, messages: [{ id: 'message', sender: 'Coach', content: 'The training candidate failed validation. A bounded correction is queued; your active plan has not changed.' }],
      requests: [{ id: 'request', kind: 'Combined', status: 'Queued', status_message: 'Correcting a candidate that failed validation (revision 1 of 2).' }],
    };
    else throw new Error(`Unexpected request: ${path}`);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Plan correction', exact: true }).click();
  await expect(page.getByText('Correcting a candidate that failed validation (revision 1 of 2).', { exact: true })).toBeVisible();
  await expect(page.getByText(/your active plan has not changed/)).toBeVisible();
});

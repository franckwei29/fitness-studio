import { test, expect } from '@playwright/test';

// Presentation fixture; backend tests cover the durable correction workflow.
for (const scenario of [
  { status: 'Queued', message: 'Correcting a candidate that failed validation (revision 1 of 2).' },
  { status: 'Stopped', message: 'This request expired. Its history is retained; submit a new request to continue.' },
]) {
test(`member sees the ${scenario.status} request explanation`, async ({ page }) => {
  const conversation = { id: 'conversation', title: 'Plan correction', active_role: 'Coach' };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if(path.endsWith('/events')){await route.fulfill({contentType:'text/event-stream',body:'retry: 1500\n\n: reconnect\n\n'});return;}
    let data: unknown;
    if (path === '/api/auth/me') data = { user: { id: 'member', display_name: 'Demo Member', role: 'Member' }, csrf_token: 'test' };
    else if (path === '/api/conversations') data = [conversation];
    else if (path === '/api/conversations/conversation') data = {
      conversation, messages: [{ id: 'message', sender: 'Coach', content: 'The training candidate failed validation. A bounded correction is queued; your active plan has not changed.' }],
      requests: [{ id: 'request', kind: 'Combined', active_role:scenario.status==='Queued'?'Coach':null, status: scenario.status, status_message: scenario.message }],
    };
    else throw new Error(`Unexpected request: ${path}`);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Plan correction', exact: true }).click();
  await expect(page.getByText(scenario.message, { exact: true })).toBeVisible();
  await expect(page.getByText(/your active plan has not changed/)).toBeVisible();
});
}

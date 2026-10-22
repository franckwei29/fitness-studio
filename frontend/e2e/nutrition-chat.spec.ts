import { test, expect } from '@playwright/test';

// This browser fixture verifies intent selection and role presentation, not Gemini output.
test('nutrition questions are distinct from nutrition plan generation', async ({ page }) => {
  let submitted: any = null;
  const conversation = { id: 'conversation', title: 'Questions', active_role: 'Nutrition' };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown;
    if (path === '/api/auth/me') data = { user: { id: 'member', display_name: 'Test Member', role: 'Member' }, csrf_token: 'test' };
    else if (path === '/api/conversations') data = route.request().method() === 'POST' ? conversation : [];
    else if (path === '/api/conversations/conversation/messages') {
      submitted = route.request().postDataJSON();
      data = { request_id: 'question' };
    } else if (path === '/api/conversations/conversation') data = {
      conversation,
      messages: submitted ? [{ id: 'message', sender: 'Nutrition', content: 'This is a question about your current meal example.' }] : [],
      requests: [],
    };
    else throw new Error(`Unexpected test request: ${path}`);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await page.getByLabel('Request type').selectOption({ label: 'Nutrition questions' });
  await page.getByLabel('Message', { exact: true }).fill('Explain the portions in my current meal example.');
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.getByRole('heading', { name: 'Nutrition guidance' })).toBeVisible();
  await expect(page.getByText('This is a question about your current meal example.')).toBeVisible();
  expect(submitted.purpose).toBe('NutritionChat');
});

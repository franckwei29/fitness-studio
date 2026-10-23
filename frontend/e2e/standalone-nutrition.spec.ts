import { test, expect } from '@playwright/test';

// Presentation fixture; database tests verify approval and activation boundaries.
test('member activates standalone nutrition without a training pair', async ({ page }) => {
  let confirmed = false;
  let submission: any = null;
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown;
    if (path === '/api/auth/me') data = { user: { id: 'member', display_name: 'Test Member', role: 'Member' }, csrf_token: 'test' };
    else if (path === '/api/conversations') data = [];
    else if (path === '/api/me/plans/nutrition/nutrition/confirm') {
      submission = route.request().postDataJSON();
      confirmed = true;
      data = { status: 'Committed', nutrition_id: 'nutrition', current_version: 1 };
    } else if (path === '/api/me/plans') data = {
      member_status: 'Accepted', catalogue_labels: {}, substitutions_enabled: false,
      current: { version: confirmed ? 1 : 0, training_id: null, nutrition_id: confirmed ? 'nutrition' : null, pair_id: null },
      pairs: [], plans: [{ id: 'nutrition', request_id: 'request', training_input_id: null, kind: 'Nutrition', version: 1,
        status: confirmed ? 'Active' : 'Validated', availability: { usable: true, reason: null },
        content: { title: 'Independent meal example', training_version_id: null, meals: [] }, rationale: 'Confirmed profile meal example' }],
    };
    else throw new Error(`Unexpected request: ${path}`);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'My plans', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Use these plans' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Use this nutrition plan' }).click();
  await expect(page.getByRole('status')).toHaveText('Your nutrition plan is now active.');
  await expect(page.getByRole('heading', { name: 'Independent meal example', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Use this nutrition plan' })).toHaveCount(0);
  expect(submission.expected_current_version).toBe(0);
  expect(submission.operation_key).toBeTruthy();
  expect(errors).toEqual([]);
});

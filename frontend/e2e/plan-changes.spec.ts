import { test, expect } from '@playwright/test';

// UI fixtures exercise comparisons; persistence and activation have separate database tests.
for (const kind of ['Training', 'Nutrition']) {
  test(`${kind} revision shows its recorded baseline and exact changed values`, async ({ page }) => {
    const writes: string[] = [];
    const before = kind === 'Training'
      ? { title: 'Routine', sessions: [{ exercises: [{ exercise_id: 'curl', rest_seconds: 60, sets: 2 }] }] }
      : { title: 'Meal example', meals: [{ name: 'Lunch', portions: [{ food_id: 'food-a', grams: '150' }] }] };
    const after = kind === 'Training'
      ? { title: 'Routine', sessions: [{ exercises: [{ exercise_id: 'curl', rest_seconds: 90, sets: 2 }] }] }
      : { title: 'Meal example', meals: [{ name: 'Lunch', portions: [{ food_id: 'food-b', grams: '160' }] }] };
    const common = { kind, request_id: 'request', status: 'Active', availability: { usable: true, reason: null }, rationale: 'Adjusted after confirmed feedback.' };
    const plans = [
      { ...common, id: 'new', version: 3, baseline_id: 'old', content: after },
      { ...common, id: 'other', version: 2, content: { title: 'Unrelated candidate' } },
      { ...common, id: 'old', version: 1, content: before },
    ];
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() !== 'GET') writes.push(path);
      let data: unknown;
      if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if (path === '/api/auth/me') data = { user: { id: 'member', display_name: 'Test Member', role: 'Member' }, csrf_token: 'test' };
      else if (path === '/api/conversations') data = [];
      else if (path === '/api/me/plans') data = { member_status: 'Accepted', catalogue_labels: { curl: 'Biceps curl', 'food-a': 'Food A', 'food-b': 'Food B' }, substitutions_enabled: false,
        current: { version: 2, training_id: kind === 'Training' ? 'new' : null, nutrition_id: kind === 'Nutrition' ? 'new' : null, pair_id: null }, plans, pairs: [] };
      else throw new Error(`Unexpected request: ${path}`);
      await route.fulfill({ json: data });
    });
    await page.goto('/');
    await page.getByRole('button', { name: 'My plans', exact: true }).click();
    await page.getByText(`Changes from ${kind} version 1`, { exact: true }).click();
    const table = page.getByRole('table');
    await expect(table).toBeVisible();
    const row = table.getByRole('row').filter({ hasText: kind === 'Training' ? 'Rest (seconds)' : 'Quantity (g)' });
    await expect(row.getByRole('cell').nth(0)).toHaveText(kind === 'Training' ? '60' : '150');
    await expect(row.getByRole('cell').nth(1)).toHaveText(kind === 'Training' ? '90' : '160');
    if (kind === 'Nutrition') {
      const food = table.getByRole('row').filter({ hasText: 'Meal 1 / Portion 1 / Food' });
      await expect(food.getByRole('cell').nth(0)).toHaveText('Food A');
      await expect(food.getByRole('cell').nth(1)).toHaveText('Food B');
    }
    await expect(table.getByText('Unrelated candidate')).toHaveCount(0);
    await expect(table.getByText('Sets', { exact: true })).toHaveCount(0);
    expect(writes).toEqual([]);
  });
}

import { test, expect } from '@playwright/test';

// Fixture test of the recovery proposal UI; approval and execution have database tests.
test('blocked rollback creates a fresh proposal without claiming service recovery', async ({ page }) => {
  let submitted: any = null;
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown;
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if (path === '/api/auth/me') data = { user: { id: 'owner', display_name: 'Studio Owner', role: 'Owner' }, csrf_token: 'test' };
    else if (path === '/api/conversations') data = [];
    else if (path === '/api/owner/groups') data = [{ id: 'canary', name: 'Canary', version: 3 }];
    else if (path === '/api/owner/agent-versions') data = { versions: [] };
    else if (path === '/api/owner/releases') data = [{ id: 'release', group_id: 'canary', status: 'RollbackBlocked', reason: 'Fixture failure', candidate: { role: 'Coach', agent_version_id: 'candidate', rollback_version_id: 'retained', observation_minutes: 30, minimum_runs: 3, error_threshold: 1 }, execution: { group_version: 2, rollback_blocked_reason: 'Newer group configuration exists.' } }];
    else if (path === '/api/owner/releases/release/recovery-proposals') {
      submitted = route.request().postDataJSON(); data = { id: 'recovery', status: 'PendingApproval' };
    } else throw new Error(`Unexpected request: ${path}`);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Agent releases', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Restore retained version' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Submit recovery for approval' })).toBeDisabled();
  await page.getByLabel('Reason for configuration recovery').fill('Restore the retained coach while preserving the latest review settings.');
  await page.getByRole('button', { name: 'Submit recovery for approval' }).click();
  await expect(page.getByText('Recovery proposal saved. Review it in Review queue, then execute the approved change in Member groups. Interrupted tasks require separate recovery verification.')).toBeVisible();
  expect(submitted.expected_group_version).toBe(3);
  expect(submitted.configuration).toBeUndefined();
});

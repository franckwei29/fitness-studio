import { test, expect } from '@playwright/test';

// Presentation fixtures; API integration tests cover ownership and worker fencing.
test('member can cancel a cycle request from the persistent request list', async ({page})=>{
  let cancelled=false;
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    let data:unknown;
    if(path==='/api/auth/me')data={user:{id:'member',display_name:'Demo Member',role:'Member'},csrf_token:'test'};
    else if(path==='/api/conversations')data=[];
    else if(path==='/api/requests/request/cancel'){
      expect(route.request().method()).toBe('POST');cancelled=true;data={status:'Cancelled'};
    }else if(path==='/api/requests')data=[{id:'request',kind:'CycleReview',status:cancelled?'Cancelled':'AwaitingConfirmation',created_at:'2026-10-23T01:00:00',status_message:cancelled?'Request cancelled. Any model call already dispatched may still incur usage; its result will not be applied.':'Your next-cycle candidate is ready.'}];
    else throw new Error(`Unexpected request: ${path}`);
    await route.fulfill({json:data});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Requests',exact:true}).click();
  await expect(page.getByText('Your next-cycle candidate is ready.')).toBeVisible();
  await page.getByRole('button',{name:'Cancel request',exact:true}).click();
  await expect(page.getByRole('heading',{name:'CycleReview · Cancelled'})).toBeVisible();
  await expect(page.getByText(/Request cancelled. Any model call/)).toBeVisible();
  await expect(page.getByRole('button',{name:'Cancel request',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Refresh requests'}).click();
  await expect(page.getByRole('heading',{name:'CycleReview · Cancelled'})).toBeVisible();
});

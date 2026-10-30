import { test, expect } from '@playwright/test';
test('member cancels a pending update with a clear business explanation', async ({page})=>{
 let cancelled=false;
 await page.route('**/api/**',async route=>{const path=new URL(route.request().url()).pathname;let data:unknown;
 if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if(path==='/api/auth/me')data={user:{id:'member',display_name:'Demo Member',role:'Member'},csrf_token:'test'};
 else if(path==='/api/conversations')data=[];
 else if(path==='/api/me/usage')data={account_used:4,account_limit:100,shared_hourly_limit:10,service_availability:'Available',next_capacity_at:null};
 else if(path==='/api/requests/request/cancel'){expect(route.request().method()).toBe('POST');cancelled=true;data={status:'Cancelled'};}
 else if(path==='/api/me/activity')data=[{id:'request',title:'Your routine review',status:cancelled?'Cancelled':'Ready for review',detail:cancelled?'Your existing plans were kept.':'Review the draft before changing your routine.',can_cancel:!cancelled,needs_user_action:!cancelled,action:'plans',created_at:'2026-10-23T01:00:00Z',stages:[]}];
 else throw new Error(`Unexpected request: ${path}`);await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'Activity',exact:true}).click();
 await expect(page.getByText('4 of 100 calls',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'Cancel update',exact:true}).click();
 await expect(page.getByText(/Calls already sent may still count/)).toBeVisible();
 await page.getByRole('button',{name:'Confirm cancellation'}).click();
 await expect(page.getByText('Your existing plans were kept.')).toBeVisible();
 await expect(page.getByRole('button',{name:'Cancel update',exact:true})).toHaveCount(0);
});

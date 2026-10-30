import { test, expect } from '@playwright/test';

test('member inspects confirmed versions and changes without restoring old facts',async({page})=>{
  const facts={available_days:4,goal:'Build consistency',dietary_restrictions:[],medical_supervision:false};
  let historyReads=0;
  await page.route('**/api/**',async route=>{
    expect(route.request().method()).toBe('GET');
    const path=new URL(route.request().url()).pathname;
    let data:unknown;
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if(path==='/api/auth/me')data={user:{id:'member',display_name:'Demo Member',role:'Member'},csrf_token:'test'};
    else if(path==='/api/conversations')data=[];
    else if(path==='/api/me/profile')data={member:{status:'Accepted',profile_version:2},snapshot:{facts}};
    else if(path==='/api/me/profile/history'){
      historyReads++;
      data={current_version:2,versions:[
        {version:2,created_at:'2026-10-23T00:00:00',source_assessment_id:'source-b',facts,changes:[{field:'available_days',before_present:true,after_present:true,before:3,after:4}]},
        {version:1,created_at:'2026-10-22T00:00:00',source_assessment_id:'source-a',facts:{...facts,available_days:3},changes:[{field:'available_days',before_present:false,after_present:true,before:null,after:3}]},
      ]};
    }else throw Error(`Unexpected request: ${path}`);
    await route.fulfill({json:data});
  });
  await page.goto('/');await page.getByRole('button',{name:'My profile',exact:true}).click();
  await expect(page.getByText('Confirmed profile version 2',{exact:true})).toBeVisible();expect(historyReads).toBe(0);
  await page.getByRole('button',{name:'View profile history',exact:true}).click();
  await page.locator('summary').filter({hasText:/^Version 2/}).click();
  const current=page.locator('details.plan-card').filter({has:page.locator('summary').filter({hasText:/^Version 2/})});
  await expect(current.getByRole('row',{name:'Available days 3 4'})).toBeVisible();
  await current.getByText('All confirmed fields in this version',{exact:true}).click();
  await expect(current.getByText('None',{exact:true})).toBeVisible();await expect(current.getByText('No',{exact:true})).toBeVisible();
  await page.locator('summary').filter({hasText:/^Version 1/}).click();
  await expect(page.getByRole('row',{name:'Available days Not recorded 3'})).toBeVisible();
  await page.getByRole('button',{name:'Hide profile history',exact:true}).click();
  await expect(page.getByText('Confirmed profile version 2',{exact:true})).toBeVisible();
});

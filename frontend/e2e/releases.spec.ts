import { test, expect } from '@playwright/test';

// UI-only fixtures; no real release, model evaluation, or configuration change.
test('owner supplies a reason before restoring a retained release',async({page})=>{
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 const release:any={id:'release',status:'Canary',reason:'Fixture canary rollout',group_id:'canary',candidate:{role:'Coach',agent_version_id:'candidate',rollback_version_id:'baseline',observation_minutes:30,minimum_runs:3,error_threshold:1},execution:{group_version:2}};
 let rollback:any=null;
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;let data:unknown;
  if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if(path==='/api/auth/me')data={user:{id:'owner',display_name:'UI Owner',role:'Owner'},csrf_token:'test'};
  else if(path==='/api/conversations')data=[];
  else if(path==='/api/owner/agent-versions')data={versions:[{id:'candidate',label:'Candidate',role:'Coach'},{id:'baseline',label:'Baseline',role:'Coach'}]};
  else if(path==='/api/owner/groups')data=[{id:'canary',name:'Canary',version:2}];
  else if(path==='/api/owner/releases')data=[release];
  else if(path==='/api/owner/releases/release/rollback'){
   rollback=route.request().postDataJSON();release.status='RolledBack';release.execution.rollback={verified:true,reason:rollback.reason,group_version:3};data=release;
  }else throw new Error(`Unexpected UI test request: ${path}`);
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'Agent releases',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Coach · Canary'})).toBeVisible();
 await page.getByRole('button',{name:'Restore retained version'}).click();expect(rollback).toBeNull();
 await page.getByLabel('Reason for rollback',{exact:true}).fill('Restore the evaluated baseline after reviewing the failed run.');
 await page.getByRole('button',{name:'Restore retained version'}).click();
 await expect(page.getByText(/Rollback verified · Configuration version 3/)).toBeVisible();
 await expect(page.getByRole('button',{name:'Restore retained version'})).toHaveCount(0);
 expect(rollback.reason).toContain('evaluated baseline');expect(errors).toEqual([]);
});

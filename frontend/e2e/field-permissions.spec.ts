import { test, expect } from '@playwright/test';

test('owner restricts field access through proposal, approval, and execution',async({page})=>{
  const baseline={capabilities:{training:true,nutrition:true,substitutions:true,reassessment_reminders:true,continuity_reminders:true,weekly_summary:true},field_permissions:{} as Record<string,string[]>,review_required:false,agent_versions:{}};
  const group={id:'group',name:'Default',version:1,configuration:structuredClone(baseline)};
  let change:any=null;
  let submitted:any=null;
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    let data:unknown;
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if(path==='/api/auth/me')data={user:{id:'owner',display_name:'Demo Owner',role:'Owner'},csrf_token:'test'};
    else if(path==='/api/conversations')data=[];
    else if(path==='/api/owner/call-limits/entry')data={scope_key:'group:group',label:'Default',calls:200,limit:200,version:1,fingerprint:'x'.repeat(64),applies_to:'Each member separately',source:'Group default',inheriting_members:1};
    else if(path==='/api/owner/groups')data=[group];
    else if(path==='/api/owner/group-policy')data={Coach:['age','goal'],TechnicalManager:[]};
    else if(path==='/api/owner/changes')data=change?[change]:[];
    else if(path==='/api/owner/groups/group/changes'){
      submitted=route.request().postDataJSON();
      change={id:'change',group_id:'group',status:'PendingApproval',reason:submitted.reason,candidate:submitted.configuration,baseline:{version:1,configuration:baseline},fingerprint:'exact-candidate',execution:null};data=change;
    }else if(path==='/api/owner/approvals')data=change?[{id:'approval',subject_kind:'GroupConfiguration',subject_id:'change',subject_version:1,fingerprint:'exact-candidate',decision:change.status==='PendingApproval'?'Pending':'Approved',created_at:'2026-10-23T00:00:00',context:{baseline:change.baseline,candidate:change.candidate,evaluation:{passed:true}}}]:[];
    else if(path==='/api/owner/changes/change/review'){
      expect(route.request().postDataJSON()).toMatchObject({decision:'Approved',fingerprint:'exact-candidate'});
      change.status='Approved';data=change;
    }else if(path==='/api/owner/changes/change/execute'){
      expect(change.status).toBe('Approved');change.status='Executed';change.execution={group_version:2};group.version=2;group.configuration=change.candidate;data=change;
    }else throw Error(`Unexpected request: ${path}`);
    await route.fulfill({json:data});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Member groups',exact:true}).click();
  await page.getByRole('button',{name:/Default.*Edit service settings/}).click();
  await expect(page.getByLabel('Coach: Age',{exact:true})).toBeDisabled();
  await page.getByLabel('Use standard Coach access').uncheck();
  await page.getByLabel('Coach: Age',{exact:true}).uncheck();
  await expect(page.getByText('This role has no access to personal profile fields.')).toBeVisible();
  await page.getByLabel('Reason for this change').fill('Restrict coach access to confirmed goals.');
  await page.getByRole('button',{name:'Submit for approval',exact:true}).click();
  await expect(page.getByText('Standard role access → Goal',{exact:true})).toBeVisible();
  expect(submitted.configuration.field_permissions).toEqual({Coach:['goal']});
  expect(group.version).toBe(1);
  await expect(page.getByRole('button',{name:'Execute approved change'})).toHaveCount(0);
  await page.getByRole('button',{name:'Review queue',exact:true}).click();
  await page.getByRole('button',{name:/Group Configuration/}).click();
  await page.getByRole('button',{name:'Approve',exact:true}).click();
  await page.getByRole('button',{name:'Member groups',exact:true}).click();
  await page.getByRole('button',{name:'Execute approved change',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Default · Executed'})).toBeVisible();
  await page.getByRole('button',{name:/Default.*Edit service settings/}).click();
  await expect(page.getByLabel('Coach: Age',{exact:true})).not.toBeChecked();
  await expect(page.getByLabel('Coach: Goal',{exact:true})).toBeChecked();
  expect(group.version).toBe(2);
});

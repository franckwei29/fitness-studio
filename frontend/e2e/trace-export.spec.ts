import { test, expect } from '@playwright/test';

for(const configured of [false,true]){
test(`owner trace download remains available with external export ${configured?'enabled':'disabled'}`,async({page})=>{
  const trace={fingerprint:'a'.repeat(64),external_export_configured:configured,request:{id:'request',kind:'Chat',status:'Waiting'},tasks:[],calls:[],events:[]};
  let exports=0;
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    let data:unknown;
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if(path==='/api/auth/me')data={user:{id:'owner',display_name:'Studio Owner',role:'Owner'},csrf_token:'test'};
    else if(['/api/conversations','/api/owner/groups'].includes(path))data=[];
    else if(path==='/api/owner/dashboard')data={members:{},requests:{Waiting:1},pending_approvals:0,unreconciled_calls:0,budgets:[],recent_requests:[{id:'request',kind:'Chat',status:'Waiting',model_calls:0}],period:{start:'2026-10-01',end:'2026-10-07',generated_at:'2026-10-07T00:00:00Z',new_members:0,plan_versions_created:0,successful_activations:0,active_members_now:0,unresolved_incidents_now:0,settled_micros:0,held_micros:0,unknown_calls:0,cost_by_role:[],cost_by_member:[],definitions:[]}};
    else if(path==='/api/owner/requests/request/trace')data=trace;
    else if(path==='/api/owner/requests/request/trace/export')data=trace;
    else if(path==='/api/owner/requests/request/trace/langsmith'){
      expect(route.request().postDataJSON()).toEqual({fingerprint:trace.fingerprint});exports++;
      data={status:'Unknown',run_id:'remote-run',message:'The remote export outcome is unknown. Local evidence is retained.'};
    }else throw Error(`Unexpected request: ${path}`);
    await route.fulfill({json:data});
  });
  await page.goto('/');await page.getByRole('button',{name:'Studio overview',exact:true}).click();
  await page.getByRole('button',{name:/Chat.*model reservations/}).click();
  const publish=page.getByRole('button',{name:'Export snapshot to LangSmith',exact:true});
  if(configured){await publish.click();await expect(page.getByText('Unknown: The remote export outcome is unknown. Local evidence is retained.',{exact:true})).toBeVisible();expect(exports).toBe(1);}
  else{await expect(publish).toBeDisabled();expect(exports).toBe(0);}
  const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Download redacted trace (.json)',exact:true}).click();
  expect((await downloaded).suggestedFilename()).toBe('trace-request.json');
});
}

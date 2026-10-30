import { test, expect } from '@playwright/test';

// UI-only fixtures. These responses are not model quality or release evidence.
test('owner reviews every comparison result before submitting quality ratings',async({page})=>{
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 const candidate={id:'candidate',role:'Coach',label:'Candidate fixture',model:'test-model',prompt:'Fixture instructions',fingerprint:'fixture',configured_groups:[]};
 const run={id:'run',baseline_id:'baseline',candidate_id:'candidate',evidence_mode:'Live',status:'AwaitingReview',cases:[{key:'case-one'}],suite_version:'ui-fixture'};
 const results=['baseline','candidate'].map((id,index)=>({id:`result-${index}`,agent_version_id:id,case_key:'case-one',passed:true,latency_ms:10,checks:{violations:[]},output:{reply:'UI fixture response'}}));
 let submitted:any=null;
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url()).pathname;
  let data:unknown;
  if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if(url==='/api/auth/me')data={user:{id:'owner',display_name:'Fixture Owner',role:'Owner'},csrf_token:'test-csrf'};
  else if(url==='/api/conversations')data=[];
  else if(url==='/api/owner/groups')data=[];
  else if(url==='/api/owner/agent-versions')data={default_model:'test-model',versions:[candidate]};
  else if(url.endsWith('/evaluations/run/review')){
   submitted=route.request().postDataJSON();run.status='Reviewed';data=run;
  }else if(url.endsWith('/evaluations/run'))data={run,results,tasks:[],usage:{calls:2,settled_micros:20,unknown_calls:0}};
  else if(url.endsWith('/evaluations'))data=[run];
  else throw new Error(`Unexpected API request in isolated UI test: ${url}`);
  await route.fulfill({json:data});
 });
 await page.goto('/');
 await page.getByRole('button',{name:'Agent versions',exact:true}).click();
 await page.locator('summary').filter({hasText:'Candidate fixture'}).click();
 await page.getByRole('button',{name:'Load comparisons'}).click();
 await page.getByRole('button',{name:/Live · Awaiting Review/}).click();
 await expect(page.getByRole('heading',{name:'Review explanation quality'})).toBeVisible();
 await page.getByRole('button',{name:'Submit quality review'}).click();
 expect(submitted).toBeNull();
 await expect(page.getByRole('combobox',{name:'Explanation quality',exact:true})).toHaveCount(2);
 for(const select of await page.getByRole('combobox',{name:'Explanation quality',exact:true}).all()){
  await expect(select).toHaveValue('');await select.selectOption('4');
 }
 for(const select of await page.getByLabel("Is the response factual and within the agent's role?").all())await select.selectOption('yes');
 for(const notes of await page.getByLabel('Review notes',{exact:true}).all())await notes.fill('The response respects the supplied evidence and role boundaries.');
 await page.getByRole('button',{name:'Submit quality review'}).click();
 await expect(page.getByText('Evidence: Live. Status: Reviewed.')).toBeVisible();
 expect(submitted.ratings).toHaveLength(2);
 expect(submitted.ratings[0]).toMatchObject({result_id:'result-0',explanation_quality:4,factual_and_role_compliant:true});
 expect(errors).toEqual([]);
});

test('incomplete comparison retains evidence but cannot be reviewed or restarted', async ({ page }) => {
  const candidate = { id: 'candidate', role: 'Coach', label: 'Incomplete fixture', model: 'test-model', prompt: 'Fixture', fingerprint: 'fixture', configured_groups: [] };
  const run = { id: 'run', baseline_id: 'baseline', candidate_id: 'candidate', evidence_mode: 'Live', status: 'Incomplete', cases: [{ key: 'case-one' }], suite_version: 'fixture' };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown;
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if (path === '/api/auth/me') data = { user: { id: 'owner', display_name: 'Fixture Owner', role: 'Owner' }, csrf_token: 'test' };
    else if (path === '/api/conversations' || path === '/api/owner/groups') data = [];
    else if (path === '/api/owner/agent-versions') data = { default_model: 'test-model', versions: [candidate] };
    else if (path.endsWith('/evaluations/run')) data = { run, results: [], tasks: [{ request_id: 'task', status: 'Cancelled', wait_reason: null }], usage: { calls: 1, settled_micros: 15, unknown_calls: 0 } };
    else if (path.endsWith('/evaluations')) data = [run];
    else throw new Error(`Unexpected request: ${path}`);
    await route.fulfill({ json: data });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Agent versions', exact: true }).click();
  await page.locator('summary').filter({ hasText: 'Incomplete fixture' }).click();
  await page.getByRole('button', { name: 'Load comparisons' }).click();
  await page.getByRole('button', { name: /Live · Incomplete/ }).click();
  await expect(page.getByRole('status')).toContainText('cannot support a release');
  await expect(page.getByText('Recorded calls: 1. Settled cost: $0.000015. Unknown-usage calls: 0.')).toBeVisible();
  await expect(page.getByRole('button', { name: /Start .*call comparison/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Submit quality review' })).toHaveCount(0);
});

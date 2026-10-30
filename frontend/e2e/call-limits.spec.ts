import {test,expect,type Page} from '@playwright/test';
async function setup(page:Page){
 const rule=(scope:string,label:string,limit:number,extra:any={})=>({scope_key:scope,label,calls:limit,limit,version:1,fingerprint:`${scope}-1`.padEnd(64,'x'),applies_to:scope.startsWith('model:')?'All studio accounts':'Each member separately',source:'Group default',...extra});
 const luna=rule('model:openai:gpt-6-luna','GPT-6 Luna',100,{model:'gpt-6-luna',provider:'openai',configured:true,used:80,source:'Studio model limit'}),gemini=rule('model:gemini:gemini-3.5-flash-lite','Gemini Flash Lite',100,{model:'gemini-3.5-flash-lite',provider:'gemini',configured:true,used:3,source:'Studio model limit'}),group=rule('group:g','Standard Members',200,{members:2,inheriting_members:2}),member=rule('member:m','Alice Chen',200,{calls:null,member_id:'m',email:'alice@example.test',group_id:'g',group_name:'Standard Members',group_limit:200,used:14}),owner=rule('owner:o','Studio Owner',200,{source:'Owner allowance'}),initial=rule('initial','Initial account allowance',200);
 const all=[luna,gemini,group,member,owner,initial],writes:any[]=[],conversation={id:'limits-chat',active_role:'TechnicalManager',title:'Studio work'};
 const messages:any[]=[{id:'q',sender:'User',content:'Set the studio-wide Luna hourly limit to 500 calls.'},{id:'a',sender:'TechnicalManager',content:'Review this model limit.',cards:[{type:'OwnerQuota',message:'Review the scope and current allowance below.',entries:[{rule:structuredClone(luna),editing:true,proposed_calls:500}]}]}];
 let stale=false;
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname;let data:any;
  if(path==='/api/auth/me')data={user:{id:'o',display_name:'Owner',role:'Owner'},csrf_token:'x'};
  else if(path==='/api/conversations')data=[conversation];
  else if(path==='/api/conversations/limits-chat')data={conversation,messages,requests:[]};
  else if(path==='/api/owner/conversations/limits-chat/receipts'){messages.push({id:`receipt-${messages.length}`,sender:'TechnicalManager',content:'AI call allowance updated: GPT-6 Luna. 100 → 500 calls.',cards:[{type:'OwnerReceipt',target:{page:'budgets',id:luna.scope_key,section:'call-limit'}}]});data={status:'Recorded'};}
  else if(path==='/api/owner/call-limits/entry')data=all.find(r=>r.scope_key===url.searchParams.get('scope'));
  else if(path==='/api/owner/call-limits'){
   if(route.request().method()==='POST'){
    const body=route.request().postDataJSON(),row=all.find(r=>r.scope_key===body.scope_key)!;writes.push(body);
    if(stale||body.expected_fingerprint!==row.fingerprint){stale=false;await route.fulfill({status:409,json:{detail:'This allowance or member group changed. Refresh before saving.'}});return;}
    const old=row.limit;row.calls=body.calls;row.limit=body.calls??group.limit;row.version++;row.fingerprint=`${row.scope_key}-${row.version}`.padEnd(64,'x');
    if(row===member)row.source=row.calls===null?'Group default':'Custom allowance';
    if(row===group){member.group_limit=row.limit;if(member.calls===null)member.limit=row.limit;}
    data={...row,message:`${row===luna?'Hourly limit':'Daily allowance'} updated from ${old} to ${row.limit} calls.`};
   }else data={models:[luna,gemini],groups:[group],members:url.searchParams.get('custom_only')==='true'&&member.calls===null?[]:[member],total:1,owner,initial,default_model:luna.model,has_more:false};
  }
  else if(path==='/api/owner/budgets')data=[{id:'b',scope_key:'global',limit_micros:100000000,spent_micros:10000,reserved_micros:20000,fingerprint:'budget-1'}];
  else if(path==='/api/owner/members')data=[{id:'m',display_name:'Alice Chen',group_id:'g',status:'Accepted',profile_version:1}];
  else if(path==='/api/owner/unknown-usage'||path==='/api/notifications')data=[];
  else if(path==='/api/me/usage')data={account_used:10,account_limit:200,shared_hourly_limit:100,service_availability:'Available',blocking_reason:null,next_capacity_at:null};
  else if(path==='/api/owner/groups')data=[{id:'g',name:'Standard Members',version:1}];
  else throw new Error(path);
  await route.fulfill({json:data});
 });
 return {writes,luna,member,setStale:()=>{stale=true;}};
}
for(const width of [390,1440])test(`editable studio limits and separate spending controls ${width}px`,async({page})=>{
 const state=await setup(page);await page.setViewportSize({width,height:960});await page.goto('/#/owner/budgets');
 await expect(page.getByRole('heading',{name:'AI usage & limits'})).toBeVisible();
 const luna=page.locator('.model-provider-card').filter({has:page.getByRole('heading',{name:'GPT-6 Luna'})});
 await expect(luna.getByText('80 / 100',{exact:true})).toBeVisible();await luna.getByRole('button',{name:'500',exact:true}).click();expect(state.writes).toHaveLength(0);
 await luna.getByRole('button',{name:'Save changes',exact:true}).click();await expect(luna.getByRole('status')).toContainText('100 to 500');await expect(luna.getByText('80 / 500',{exact:true})).toBeVisible();
 expect(state.writes[0]).toMatchObject({scope_key:'model:openai:gpt-6-luna',calls:500});await expect(page.getByText('No automatic reset',{exact:false}).first()).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test('member allowance inheritance and restore without typing IDs',async({page})=>{
 const state=await setup(page);await page.goto('/#/owner/budgets');
 const alice=page.locator('.allowance-members article').filter({has:page.getByRole('heading',{name:'Alice Chen'})});await alice.getByRole('button',{name:'Edit allowance',exact:true}).click();
 await alice.getByLabel('Allowance rule').selectOption('custom');await alice.getByRole('spinbutton').fill('300');await alice.getByRole('button',{name:'Save changes',exact:true}).click();await expect(alice.getByText('Custom allowance',{exact:true}).first()).toBeVisible();expect(state.member.calls).toBe(300);
 await alice.getByLabel('Allowance rule').selectOption('inherit');await alice.getByRole('button',{name:'Restore group default',exact:true}).click();expect(state.member.calls).toBeNull();await expect(alice.getByRole('status')).toContainText('300 to 200');
});
test('stale model edits keep the draft and require deliberate refresh',async({page})=>{
 const state=await setup(page);await page.goto('/#/owner/budgets');const luna=page.locator('.model-provider-card').first();await luna.getByLabel('Hourly limit',{exact:true}).fill('500');state.setStale();await luna.getByRole('button',{name:'Save changes',exact:true}).click();await expect(luna.getByRole('alert')).toContainText('changed');await expect(luna.getByLabel('Hourly limit',{exact:true})).toHaveValue('500');await luna.getByRole('button',{name:'Refresh current allowance'}).click();await luna.getByRole('button',{name:'Save changes',exact:true}).click();await expect(luna.getByRole('status')).toContainText('100 to 500');
});
test('quota confirmation stays inside one main conversation and opens exact settings',async({page})=>{
 const state=await setup(page);await page.goto('/#/assistant/limits-chat');const card=page.getByRole('region',{name:'AI call allowances'});await expect(card.getByText('All studio accounts',{exact:false})).toBeVisible();expect(state.writes).toHaveLength(0);await card.getByRole('button',{name:'Confirm change',exact:true}).click();await expect(card.getByRole('button',{name:'Confirm change',exact:true})).toHaveCount(0);await expect(page.getByText('AI call allowance updated: GPT-6 Luna. 100 → 500 calls.',{exact:true})).toBeVisible();await card.getByRole('button',{name:'Open settings'}).click();await expect(page).toHaveURL(/section=call-limit/);await expect(page.getByRole('spinbutton',{name:'Hourly limit'})).toHaveValue('500');await page.getByRole('button',{name:'Back to conversation',exact:true}).click();await expect(page.getByPlaceholder('What would you like help with?')).toBeVisible();expect(state.writes).toHaveLength(1);await page.reload();await expect(page.getByRole('button',{name:'Confirm change',exact:true})).toHaveCount(0);
});

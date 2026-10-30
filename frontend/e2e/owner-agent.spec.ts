import {test,expect,type Page} from '@playwright/test';
// UI-only fixtures; SQL fences and model behavior are checked separately.
async function setup(page:Page){
 const conversation={id:'owner-agent-main',title:'Studio decisions',active_role:'TechnicalManager'};
 const task:any={id:'task-one',conversation_id:conversation.id,goal:'Make Coach explanations clearer',status:'Ready',fingerprint:'a'.repeat(64),actions:[{name:'candidate.save',target:'',arguments:JSON.stringify({role:'Coach',label:'Clearer coaching',model:'test-model',instructions:'Shorten introductions. Keep exercise instructions detailed.'})}],changes:['Keep the current model and all validation rules.','Shorten introductory explanations; preserve detailed exercise instructions.','Save a candidate only. Nothing will be deployed.'],remaining:['Compare with the current version before proposing a release.'],evidence:[{title:'Review group settings',page:'groups',id:'default'},{title:'Inspect current agent versions',page:'versions'}],receipts:[]};
 const config={capabilities:{training:true,nutrition:true,substitutions:true,reassessment_reminders:true,continuity_reminders:true,weekly_summary:true},review_required:true,field_permissions:{},agent_versions:{}};
 const messages:any[]=[{id:'user-one',sender:'User',content:'Make the Coach introduction shorter. Keep instructions detailed and show me the changes first.'},{id:'agent-one',sender:'TechnicalManager',content:'I have prepared a focused candidate. The model, exercise instructions and approval rules stay the same. Review the scope below, then confirm when you are ready.',cards:[{type:'OwnerTask',request_id:task.id}]}];
 const state={executions:0,submissions:[] as any[],groupWrites:0};
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;let data:any;
  if(path==='/api/auth/me')data={user:{id:'owner',display_name:'Studio Owner',role:'Owner'},csrf_token:'test'};
  else if(path==='/api/owner/updates')data={needs_action:0,items:[]};
  else if(path==='/api/conversations')data=[conversation];
  else if(path==='/api/conversations/'+conversation.id)data={conversation,messages,requests:[]};
  else if(path===`/api/conversations/${conversation.id}/messages`){state.submissions.push(route.request().postDataJSON());data={status:'Queued'};}
  else if(path==='/api/owner/tasks/task-one')data=task;
  else if(path==='/api/owner/tasks/task-one/decision'){state.executions++;const body=route.request().postDataJSON();task.status=body.decision==='Execute'?'Executed':'Dismissed';task.receipts=[{action:'candidate.save',target:{page:'versions',id:'candidate'},status:'Saved'}];data=task;}
  else if(path.includes('/receipts'))data={recorded:false};
  else if(path==='/api/owner/groups')data=[{id:'default',name:'Default',version:1,configuration:config}];
  else if(path==='/api/owner/changes')data=[];
  else if(path==='/api/owner/group-policy')data={Coach:['age','goal','experience','available_days','session_minutes','equipment'],Nutrition:['age','goal','dietary_restrictions'],TechnicalManager:[]};
  else if(path==='/api/owner/call-limits/entry')data={scope_key:'group:default',label:'Default',calls:200,limit:200,version:1,fingerprint:'b'.repeat(64),source:'Group',applies_to:'Default members',inheriting_members:1};
  else if(path==='/api/owner/groups/default/changes'){state.groupWrites++;data={id:'change',status:'PendingApproval',group_id:'default',reason:route.request().postDataJSON().reason,baseline:{version:1,configuration:config},candidate:route.request().postDataJSON().configuration,execution:null};}
  else if(path==='/api/owner/agent-versions')data={default_model:'test-model',default_provider:'openai',models:[],versions:[]};
  else throw new Error('Unexpected fixture '+path);
  await route.fulfill({json:data});
 });
 await page.goto('/#/assistant/'+conversation.id);
 await expect(page.getByRole('heading',{name:'Make Coach explanations clearer'})).toBeVisible();
 return state;
}
test('work panel preserves conversation and guards unsaved edits',async({page})=>{
 const state=await setup(page);
 const composer=page.getByRole('textbox',{name:'Message',exact:true});
 await composer.fill('Keep the original model when you revise this.');
 await page.getByRole('button',{name:'Review group settings'}).click();
 const panel=page.getByRole('dialog',{name:'Studio work panel'});
 await expect(panel).toBeVisible();
 await expect(page).toHaveURL(/assistant\/owner-agent-main/);
 await panel.getByLabel('Require owner review before plan activation').uncheck();
 await panel.getByRole('button',{name:'Close work panel'}).click();
 await expect(panel.getByText('You have unsaved edits.')).toBeVisible();
 await panel.getByRole('button',{name:'Keep editing',exact:true}).click();
 await expect(panel.getByLabel('Require owner review before plan activation')).not.toBeChecked();
 await page.screenshot({path:'../.local/owner-agent-panel.png',fullPage:true});
 await panel.getByRole('button',{name:'Close work panel'}).click();
 await panel.getByRole('button',{name:'Discard edits',exact:true}).click();
 await expect(panel).toHaveCount(0);
 await expect(composer).toHaveValue('Keep the original model when you revise this.');
 expect(state.groupWrites).toBe(0);
});
test('explicit conversational confirmation executes only once',async({page})=>{
 const state=await setup(page);
 await page.getByRole('textbox',{name:'Message',exact:true}).fill('Confirm and run');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByText('Actions recorded',{exact:true})).toBeVisible();
 expect(state.executions).toBe(1);
 expect(state.submissions).toHaveLength(0);
 await expect(page.getByRole('button',{name:'Confirm and run',exact:true})).toHaveCount(0);
});
test('mobile task card is readable and ordinary instructions use agent mode',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 const state=await setup(page);
 await expect(page.getByRole('button',{name:'Confirm and run',exact:true})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
 await page.screenshot({path:'../.local/owner-agent-mobile.png',fullPage:true});
 await page.getByRole('textbox',{name:'Message',exact:true}).fill('Keep exercise instructions detailed. Only shorten the introduction.');
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect.poll(()=>state.submissions.length).toBe(1);
 expect(state.submissions[0].owner_agent).toBe(true);
 expect(state.executions).toBe(0);
});

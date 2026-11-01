import {test,expect} from '@playwright/test';

test('one confirmation starts comparison and results stay in chat across reload',async({page})=>{
 let executions=0;let reads=0;
 const conversation={id:'compare-chat',title:'Compare models',active_role:'TechnicalManager'};
 const task:any={id:'compare-task',conversation_id:conversation.id,goal:'Compare Canary Coach with Gemini',status:'Ready',fingerprint:'snapshot',actions:[{name:'comparison.run_current',target:'',arguments:JSON.stringify({group_id:'canary',role:'Coach',model:'gemini-3.5-flash-lite'})}],changes:[],remaining:[],evidence:[],receipts:[],comparisons:[{group_name:'Canary',role:'Coach',baseline_model:'gpt-6-luna',model:'gemini-3.5-flash-lite',planned_calls:6,cases_per_model:3}]};
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;let data:any;
  if(path==='/api/auth/me')data={user:{id:'owner',display_name:'Owner',role:'Owner'},csrf_token:'test'};
  else if(path==='/api/owner/groups')data=[];
  else if(path==='/api/owner/updates')data={needs_action:0,items:[]};
  else if(path==='/api/conversations')data=[conversation];
  else if(path==='/api/conversations/compare-chat')data={conversation,messages:[{id:'m',sender:'TechnicalManager',content:'Confirm to start the comparison.',cards:[{type:'OwnerTask',request_id:task.id}]}],requests:[]};
  else if(path==='/api/owner/tasks/compare-task')data=task;
  else if(path==='/api/owner/tasks/compare-task/decision'){executions++;task.status='Executed';task.receipts=[{action:'comparison.run_current',target:{page:'versions',id:'candidate',section:'evaluation:run'},status:'Running'}];data=task;}
  else if(path==='/api/owner/agent-versions/candidate/evaluations/run'){reads++;data={run:{id:'run',status:reads>1?'Completed':'Running',baseline_id:'baseline',candidate_id:'candidate',evidence_mode:'Live',cases:[{key:'short-session'}],suite_version:'test'},models:[{version_id:'baseline',model:'gpt-6-luna',provider:'openai',passed:reads>1?1:0,recorded:reads>1?1:0,expected:1,average_latency_ms:null,average_cost_micros:null}],results:[],tasks:[],usage:{calls:reads>1?2:0,settled_micros:0,unknown_calls:0}};}
  else if(path.includes('/receipts'))data={recorded:false};
  else throw Error('Unexpected API '+path);
  await route.fulfill({json:data});
 });
 await page.goto('/#/assistant/compare-chat');
 await expect(page.getByText('6 planned calls',{exact:false})).toBeVisible();
 await expect(page.getByRole('combobox',{name:/Compare against/})).toHaveCount(0);
 await page.getByRole('button',{name:'Confirm and run',exact:true}).click();
 await expect(page.getByText('Comparison complete',{exact:true})).toBeVisible({timeout:10000});
 expect(executions).toBe(1);
 await expect(page.getByRole('button',{name:/^Start .*comparison/})).toHaveCount(0);
 await expect(page).toHaveURL(/assistant\/compare-chat/);
 await page.reload();
 await expect(page.getByRole('heading',{name:'Model comparison',exact:true})).toBeVisible();
 expect(executions).toBe(1);
});

test('invalid historical comparison cannot be confirmed and can be repaired inside chat',async({page})=>{
 let repairs=0;
 const conversation={id:'invalid-chat',title:'Comparison repair',active_role:'TechnicalManager'};
 const task={id:'invalid-task',conversation_id:conversation.id,goal:'Compare Canary Coach with Gemini',status:'NeedsCorrection',validation_error:'Comparison needs existing version IDs, not group IDs.',fingerprint:'old',actions:[{name:'comparison.prepare',target:'canary',arguments:'{}'}],changes:[],remaining:[],evidence:[],receipts:[]};
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;let data:any;
  if(path==='/api/auth/me')data={user:{id:'owner',display_name:'Owner',role:'Owner'},csrf_token:'test'};
  else if(path==='/api/owner/groups')data=[];
  else if(path==='/api/owner/updates')data={needs_action:0,items:[]};
  else if(path==='/api/conversations')data=[conversation];
  else if(path==='/api/conversations/invalid-chat')data={conversation,messages:[{id:'m',sender:'TechnicalManager',content:'Old proposal',cards:[{type:'OwnerTask',request_id:task.id}]}],requests:[]};
  else if(path==='/api/owner/tasks/invalid-task')data=task;
  else if(path==='/api/conversations/invalid-chat/messages'){repairs++;expect(route.request().postDataJSON().owner_agent).toBe(true);data={status:'Queued'};}
  else throw Error('Unexpected API '+path);
  await route.fulfill({json:data});
 });
 await page.goto('/#/assistant/invalid-chat');
 await expect(page.getByRole('button',{name:'Confirm and run',exact:true})).toHaveCount(0);
 await expect(page.getByText(task.validation_error,{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Prepare updated proposal in chat'}).click();
 await expect.poll(()=>repairs).toBe(1);
 await expect(page).toHaveURL(/assistant\/invalid-chat/);
});

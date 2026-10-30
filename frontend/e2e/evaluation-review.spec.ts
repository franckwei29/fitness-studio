import {test,expect} from '@playwright/test';
// UI-only fixtures, not model quality evidence.
for(const status of ['Completed','Incomplete'])test(`comparison ${status}: no ratings and explicit decision`,async({page})=>{
 const candidate={id:'candidate',role:'Coach',label:'Gemini trial',model:'gemini-test',prompt:'Fixture',fingerprint:'fixture',configured_groups:[]};
 const run={id:'run',baseline_id:'current',candidate_id:'candidate',evidence_mode:'Live',status,cases:[{key:'case-one'}],suite_version:'fixture'};
 let proposals=0;
 await page.route('**/api/**',async route=>{
 const path=new URL(route.request().url()).pathname;let data:unknown;
 if(path==='/api/auth/me')data={user:{id:'owner',display_name:'Owner',role:'Owner'},csrf_token:'fixture'};
 else if(path==='/api/owner/updates')data={needs_action:0,items:[]};
 else if(path==='/api/conversations')data=[];
 else if(path==='/api/owner/groups')data=[{id:'canary',name:'Canary',version:1}];
 else if(path==='/api/owner/agent-versions')data={default_model:'luna-test',versions:[candidate]};
 else if(path.endsWith('/evaluations/run'))data={run,models:['current','candidate'].map((id,i)=>({version_id:id,model:i?'gemini-test':'luna-test',provider:i?'gemini':'openai',passed:1,expected:1,recorded:1,average_latency_ms:1000,average_cost_micros:status==='Completed'?10:null})),results:['current','candidate'].map(id=>({id,agent_version_id:id,case_key:'case-one',passed:true,checks:{violations:[]},output:{explanation:'An understandable plan.'}})),tasks:[],usage:{calls:2,settled_micros:20,unknown_calls:0}};
 else if(path.endsWith('/evaluations'))data=[run];
 else if(path==='/api/owner/releases'&&route.request().method()==='POST'){proposals++;data={id:'release'};}
 else if(path.includes('/receipts'))data={recorded:false};
 else throw Error('Unexpected fixture '+path);
 await route.fulfill({json:data});});
 await page.goto('/#/owner/versions/candidate?section=evaluation:run');
 await expect(page.getByRole('heading',{name:'Model comparison',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Submit quality review'})).toHaveCount(0);
 await page.getByText('Compare plan example · case-one').click();
 await expect(page.getByText('An understandable plan.',{exact:true})).toHaveCount(2);
 if(status==='Completed'){
 expect(proposals).toBe(0);await page.getByRole('button',{name:'Prepare Canary release'}).click();
 await expect(page.getByRole('button',{name:'Review release and confirm'})).toBeVisible();expect(proposals).toBe(1);
 }else{await expect(page.getByRole('button',{name:'Prepare Canary release'})).toHaveCount(0);await expect(page.getByText('Awaiting complete costs')).toHaveCount(2);}
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
 await page.screenshot({path:`../.local/model-comparison-${status}.png`,fullPage:true});
});

test('several member assignments require confirmation',async({page})=>{
 let saves=0;let body:any;
 const members=Array.from({length:3},(_,i)=>({id:`m${i}`,display_name:`Member ${i+1}`,group_id:'default',status:'Accepted',profile_version:1}));
 await page.route('**/api/**',async route=>{
 const path=new URL(route.request().url()).pathname;let data:unknown;
 if(path==='/api/auth/me')data={user:{id:'owner',display_name:'Owner',role:'Owner'},csrf_token:'fixture'};
 else if(path==='/api/owner/updates')data={needs_action:0,items:[]};
 else if(path==='/api/conversations')data=[];
 else if(path==='/api/owner/groups')data=[{id:'default',name:'Default',version:1,configuration:{}},{id:'canary',name:'Canary',version:2,configuration:{}}];
 else if(path==='/api/owner/members')data=members;
 else if(path==='/api/owner/members/assign-group'){saves++;body=route.request().postDataJSON();data={assigned_count:body.members.length};}
 else if(path.includes('/receipts'))data={recorded:false};
 else throw Error('Unexpected fixture '+path);
 await route.fulfill({json:data});});
 await page.goto('/#/owner/members');
 await page.getByText('Assign members to a service group',{exact:true}).click();
 for(let i=1;i<=3;i++)await page.getByLabel(`Member ${i} · Default`,{exact:true}).check();
 await page.getByLabel('Destination group').selectOption('canary');
 await page.getByLabel('Reason',{exact:true}).fill('Try the evaluated Coach model');
 await page.getByRole('button',{name:'Review assignments',exact:true}).click();expect(saves).toBe(0);
 await expect(page.getByRole('heading',{name:'Confirm 3 member assignments'})).toBeVisible();
 await page.getByRole('button',{name:'Confirm member assignments',exact:true}).click();
 await expect(page.getByRole('status')).toContainText('3 member assignments saved');expect(body.members).toHaveLength(3);expect(body.expected_group_version).toBe(2);
});

test('fresh workshop prepares the effective Coach without saved versions',async({page})=>{
 let prepared:any=null;
 await page.route('**/api/**',async route=>{
 const path=new URL(route.request().url()).pathname;let data:unknown;
 if(path==='/api/auth/me')data={user:{id:'owner',display_name:'Owner',role:'Owner'},csrf_token:'fixture'};
 else if(path==='/api/owner/updates')data={needs_action:0,items:[]};
 else if(path==='/api/conversations')data=[];
 else if(path==='/api/owner/groups')data=[{id:'canary',name:'Canary',version:1}];
 else if(path==='/api/owner/agent-versions')data={default_model:'gpt-6-luna',default_provider:'openai',versions:[],models:[{id:'gemini-test',provider:'gemini',label:'Google Gemini',configured:true,reasoning:[]}]};
 else if(path==='/api/owner/agent-versions/compare-current'){prepared=route.request().postDataJSON();data={id:'run',candidate_id:'candidate'};}
 else if(path.includes('/receipts'))data={recorded:false};
 else throw Error('Unexpected fixture '+path);
 await route.fulfill({json:data});});
 await page.goto('/#/owner/versions');
 await page.getByRole('combobox',{name:'Model to compare',exact:true}).selectOption('gemini-test');
 await page.getByRole('button',{name:'Prepare model comparison',exact:true}).click();
 await expect(page.getByRole('button',{name:'Open prepared comparison'})).toBeVisible();
 expect(prepared).toEqual({group_id:'canary',role:'Coach',model:'gemini-test'});
});

test('keeping the current model records a decision without preparing a release',async({page})=>{
 let kept=false;let releaseWrites=0;
 const run:any={id:'run',baseline_id:'current',candidate_id:'candidate',evidence_mode:'Live',status:'Reviewed',cases:[],suite_version:'fixture',summary:{}};
 await page.route('**/api/**',async route=>{
 const path=new URL(route.request().url()).pathname;let data:unknown;
 if(path==='/api/auth/me')data={user:{id:'owner',display_name:'Owner',role:'Owner'},csrf_token:'fixture'};
 else if(path==='/api/owner/updates')data={needs_action:kept?0:1,items:[]};
 else if(path==='/api/conversations'||path==='/api/owner/groups')data=[];
 else if(path==='/api/owner/agent-versions')data={default_model:'luna',versions:[{id:'candidate',role:'Coach',label:'Gemini trial',model:'gemini',prompt:'Fixture',configured_groups:[]}]};
 else if(path.endsWith('/evaluations/run/keep-current')){expect(route.request().postDataJSON()).toEqual({fingerprint:'exact-comparison'});kept=true;run.summary={owner_decision:{choice:'KeepCurrent'}};data=run;}
 else if(path.endsWith('/evaluations/run'))data={run,decision_fingerprint:'exact-comparison',release:null,models:[],results:[],tasks:[],usage:{calls:0,settled_micros:0,unknown_calls:0}};
 else if(path==='/api/owner/releases'){releaseWrites++;data={};}
 else if(path.includes('/receipts'))data={recorded:false};
 else throw Error('Unexpected fixture '+path);
 await route.fulfill({json:data});});
 await page.goto('/#/owner/versions/candidate?section=evaluation:run');
 await page.getByRole('button',{name:'Keep current model',exact:true}).click();
 await expect(page.getByRole('status')).toContainText('This comparison is resolved in Updates');
 await expect(page.getByRole('button',{name:'Current model kept'})).toBeDisabled();
 await page.reload();
 await expect(page.getByRole('status')).toContainText('No service configuration was changed');
 expect(releaseWrites).toBe(0);
});

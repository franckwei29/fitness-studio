import {test,expect} from '@playwright/test';
for(const width of [390,1440])test(`member decides training before meals and opens exact drafts ${width}`,async({page},testInfo)=>{
 await page.setViewportSize({width,height:950});
 let stage='TrainingDiscussion',rev=3,trainingId='t1',nutritionCalls=0;
 const training=(id:string)=>({id,kind:'Training',version:2,status:'Validated',request_id:'r',fingerprint:id+'-fp',availability:{usable:true},content:{title:'Simple training draft',sessions:[]},rationale:'Shorter sessions.'});
 const meal={id:'n',kind:'Nutrition',version:2,status:'Validated',request_id:'r',fingerprint:'n-fp',availability:{usable:true},content:{title:'Saved meals',meals:[]},rationale:'Meals can stay unchanged.'};
 const flow=()=>({id:'f',request_id:'r',stage,revision:rev,training_id:trainingId,nutrition_id:stage==='FinalReview'?'n':null,pair_id:stage==='FinalReview'?'p':null,actions:stage==='TrainingDiscussion'?['endorse_training','revise_training','cancel']:['cancel'],goal:'Simplify training within 30 minutes.',status:'AwaitingConfirmation',baseline_version:1});
 await page.route('**/api/**',async route=>{const p=new URL(route.request().url()).pathname;let data:any=[];
 if(p.endsWith('/events')){await route.fulfill({contentType:'text/event-stream',body:': reconnect\n\n'});return;}
 if(p==='/api/auth/me')data={user:{id:'u',role:'Member',display_name:'Member'},csrf_token:'x'};
 if(p==='/api/conversations')data=[{id:'c',title:'Training discussion',active_role:'Coach'}];
 if(p==='/api/conversations/c')data={conversation:{id:'c',title:'Training discussion',active_role:stage==='TrainingDiscussion'?'Coach':'Nutrition'},workflows:[flow()],messages:[{id:'old',sender:'Nutrition',content:'Earlier meal discussion.'},{id:'user',sender:'User',content:'Simplify training within 30 minutes.'},{id:'current',sender:stage==='TrainingDiscussion'?'Coach':'Nutrition',content:'Review the current step.',cards:[{type:'Workflow',workflow_id:'f',revision:rev,stage}]}],requests:[{id:'r',kind:'Combined',status:'AwaitingConfirmation'}]};
 if(p.startsWith('/api/me/plan-drafts/')){const id=p.split('/').at(-1)!;data={plan:id==='n'?meal:training(id),plans:[training(trainingId),meal],labels:{},workflow:flow(),pairs:[{id:'p',training_id:trainingId,nutrition_id:'n'}]};}
 if(p==='/api/me/plans')data={plans:[training(trainingId),meal],pairs:[{id:'p',training_id:trainingId,nutrition_id:'n'}],catalogue_labels:{},current:{version:1},workflows:{r:flow()}};
 if(p==='/api/me/workflows/f/actions'){const b=route.request().postDataJSON();if(b.action==='revise_training'){trainingId='t2';rev++;}if(b.action==='endorse_training'){expect(b.target_id).toBe(trainingId);nutritionCalls++;stage='FinalReview';rev++;}data=flow();}
 await route.fulfill({json:data});});
 await page.goto('/#/assistant/c');await expect(page.getByText('Let’s discuss your training',{exact:true})).toBeVisible();expect(nutritionCalls).toBe(0);await page.screenshot({path:testInfo.outputPath(`training-discussion-${width}.png`),fullPage:true});
 await page.getByRole('button',{name:'Request training changes',exact:true}).click();await page.getByLabel('Changes to this draft').fill('Remove one exercise.');await page.getByRole('button',{name:'Send feedback'}).click();await expect.poll(()=>rev).toBe(4);expect(nutritionCalls).toBe(0);
 await page.getByRole('button',{name:'View training draft',exact:true}).click();await expect(page).toHaveURL(/section=draft%3At2/);await expect(page.getByRole('heading',{name:'Training draft',exact:true})).toBeVisible();await page.screenshot({path:testInfo.outputPath(`training-draft-${width}.png`),fullPage:true});await expect(page.getByText('Your current routine',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Continue with this training'}).click();await expect.poll(()=>nutritionCalls).toBe(1);
 await page.getByRole('button',{name:'Review final changes'}).click();await expect(page).toHaveURL(/section=pair%3Ap/);await expect(page.getByRole('heading',{name:'Your final update',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Confirm update',exact:true})).toBeVisible();await page.reload();await expect(page.getByRole('heading',{name:'Your final update',exact:true})).toBeVisible();expect(nutritionCalls).toBe(1);
});

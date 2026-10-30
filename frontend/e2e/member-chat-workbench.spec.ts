import {test,expect} from '@playwright/test';
for(const width of [390,1440])test(`meal replacement remains in chat with explicit preview ${width}`,async({page},info)=>{
 await page.setViewportSize({width,height:1000});let confirms=0;let card:any={type:'MemberAction',kind:'Substitution',state:'Pending',revision:1,details:{plan_kind:'Nutrition',selections:[{meal_index:0,portion_index:0}],preference:'Replace breakfast apple with banana; keep other foods unchanged.'},labels:{apple:'Apple'},plans:[{id:'n',kind:'Nutrition',content:{meals:[{name:'Breakfast',portions:[{food_id:'apple',grams:100}]}]}}]};
 await page.route('**/api/**',async r=>{const path=new URL(r.request().url()).pathname;let data:any=[];
 if(path==='/api/auth/me')data={user:{id:'member',display_name:'Member',role:'Member'},csrf_token:'x'};
 if(path==='/api/conversations')data=[{id:'c',title:'Meal changes',active_role:'Nutrition'}];
 if(path==='/api/conversations/c')data={conversation:{id:'c',title:'Meal changes',active_role:'Nutrition'},messages:[{id:'u',sender:'User',content:'Replace breakfast apple with banana.'},{id:'m',sender:'Nutrition',content:'Review the change below.',cards:[card]}],requests:[]};
 if(path==='/api/conversations/c/member-actions/m'){const b=r.request().postDataJSON();expect(b.revision).toBe(card.revision);if(b.action==='edit')card={...card,details:b.details,revision:card.revision+1};if(b.action==='confirm'){confirms++;card={...card,state:'Completed',revision:card.revision+1};}data=card;}
 if(path==='/api/me/plans')data={plans:[],pairs:[],catalogue_labels:{},member_status:'Accepted',current:{version:1}};
 await r.fulfill({json:data});});
 await page.goto('/#/assistant/c');await expect(page.getByText('Breakfast · Apple · 100 g')).toBeVisible();await expect(page.getByText('REQUESTED CHANGE')).toBeVisible();await page.getByRole('button',{name:'Edit details',exact:true}).click();await expect(page.getByRole('dialog',{name:'Your meal replacement'})).toBeVisible();await page.getByLabel('Your preferred replacement').fill('Banana for breakfast, please.');await page.screenshot({path:info.outputPath(`replacement-editor-${width}.png`),fullPage:true});await page.getByRole('button',{name:'Update preview'}).click();expect(confirms).toBe(0);await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.getByText('Banana for breakfast, please.')).toBeVisible();await page.getByRole('button',{name:'Prepare replacement'}).click();await expect(page.getByText('Request submitted. Follow the next update in this conversation.')).toBeVisible();expect(confirms).toBe(1);await expect(page).toHaveURL(/#\/assistant\/c$/);await page.reload();await expect(page.getByText('Request submitted. Follow the next update in this conversation.')).toBeVisible();await expect(page.getByRole('button',{name:'Prepare replacement'})).toHaveCount(0);
});
test('exact draft opens in modal without losing message input',async({page})=>{
 await page.route('**/api/**',async r=>{const p=new URL(r.request().url()).pathname;let data:any=[];
 if(p==='/api/auth/me')data={user:{id:'member',display_name:'Member',role:'Member'},csrf_token:'x'};
 if(p==='/api/conversations')data=[{id:'c',title:'Training',active_role:'Coach'}];
 if(p==='/api/conversations/c')data={conversation:{id:'c',title:'Training',active_role:'Coach'},messages:[{id:'m',sender:'Coach',content:'Your draft is ready.',cards:[{type:'TrainingCandidate',plan_id:'t'}]}],requests:[]};
 if(p==='/api/me/plans')data={plans:[],pairs:[],catalogue_labels:{}};
 if(p==='/api/me/plan-drafts/t')data={plan:{id:'t',kind:'Training',version:2,status:'Validated',content:{title:'Short training',sessions:[]},rationale:'Longer rests',availability:{usable:true}},plans:[],labels:{},pairs:[]};
 await r.fulfill({json:data});});
 await page.goto('/#/assistant/c');await page.getByLabel('Message',{exact:true}).fill('Keep my draft message');await page.getByRole('button',{name:'Review draft plans'}).click();await expect(page.getByRole('dialog',{name:'Your plans'})).toBeVisible();await expect(page.getByRole('heading',{name:'Training draft',exact:true})).toBeVisible();await expect(page).toHaveURL(/#\/assistant\/c$/);await page.getByRole('button',{name:'Close dialog'}).click();await expect(page.getByLabel('Message',{exact:true})).toHaveValue('Keep my draft message');
});
test('profile confirmation stays inline and cannot be submitted twice',async({page})=>{
 let submitted=0;const facts={age:30,goal:'Build a routine',experience:'Beginner',available_days:2,session_minutes:30,equipment:['Dumbbells'],dietary_restrictions:[],prescribed_exercise:false,prescribed_nutrition:false,medical_supervision:false,pregnancy_postpartum:false,postoperative_acute_injury:false,resolved_past_injury:true};
 await page.route('**/api/**',async r=>{const p=new URL(r.request().url()).pathname;let data:any=[];
 if(p==='/api/auth/me')data={user:{id:'member',display_name:'Member',role:'Member'},csrf_token:'x'};
 if(p==='/api/conversations')data=[{id:'c',title:'Profile',active_role:'Intake'}];
 if(p==='/api/conversations/c')data={conversation:{id:'c',title:'Profile',active_role:'Intake'},messages:[{id:'m',sender:'Intake',content:'Review your details.',cards:[{type:'IntakeReview'}]}],requests:[]};
 if(p==='/api/me/profile')data={member:{status:submitted?'Suspended':'Collecting'},snapshot:null,assessment:{id:'a',version:submitted?2:1,status:submitted?'PendingOwnerReview':'Collecting',confirmed_facts:submitted?facts:{},proposed_facts:facts}};
 if(p==='/api/assessments/a/confirm'){submitted++;expect(r.request().postDataJSON().facts).toEqual(facts);data={status:'PendingOwnerReview'};}
 await r.fulfill({json:data});});
 await page.goto('/#/assistant/c');await expect(page.getByRole('button',{name:'Confirm my details'})).toBeDisabled();await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Confirm my details'}).click();await expect(page.getByText('Submitted · Waiting for studio review')).toBeVisible();expect(submitted).toBe(1);await expect(page.getByRole('button',{name:'Confirm my details'})).toHaveCount(0);await expect(page).toHaveURL(/#\/assistant\/c$/);
});
test('final confirmation activates from the conversation only on explicit action',async({page})=>{
 let activated=0;
 await page.route('**/api/**',async r=>{const p=new URL(r.request().url()).pathname;let data:any=[];
 if(p==='/api/auth/me')data={user:{id:'member',display_name:'Member',role:'Member'},csrf_token:'x'};
 if(p==='/api/conversations')data=[{id:'c',title:'Final review',active_role:'Coach'}];
 if(p==='/api/conversations/c')data={conversation:{id:'c',title:'Final review',active_role:'Coach'},messages:[{id:'m',sender:'Coach',content:'Review the final update.',cards:[{type:'Workflow',workflow_id:'f',revision:1,stage:activated?'Completed':'FinalReview'}]}],requests:[],workflows:[{id:'f',request_id:'r',revision:1,stage:activated?'Completed':'FinalReview',training_id:'t',baseline_version:4,goal:'Longer rests',actions:[],status:activated?'Completed':'AwaitingConfirmation'}]};
 if(p==='/api/me/plans/training/t/confirm'){activated++;expect(r.request().postDataJSON().expected_current_version).toBe(4);data={status:'Active'};}
 await r.fulfill({json:data});});
 await page.goto('/#/assistant/c');expect(activated).toBe(0);await page.getByRole('button',{name:'Confirm update',exact:true}).click();await expect(page.getByRole('heading',{name:'Update complete',exact:true})).toBeVisible();expect(activated).toBe(1);await expect(page.getByRole('button',{name:'Confirm update',exact:true})).toHaveCount(0);
});

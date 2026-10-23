import {test,expect} from '@playwright/test';
for(const width of [320,390,768,1024,1440])test(`Sage plans and activity are readable at ${width}px`,async({page})=>{
 await page.setViewportSize({width,height:900});
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{const p=new URL(route.request().url()).pathname;let data:unknown;
 if(p==='/api/auth/me')data={user:{id:'m',display_name:'Member',role:'Member'},csrf_token:'x'};
 else if(p==='/api/conversations')data=[];
 else if(p==='/api/me/plans')data={member_status:'Accepted',catalogue_labels:{oats:'Rolled oats'},substitutions_enabled:false,current:{version:1,nutrition_id:'n'},pairs:[],plans:[{id:'n',request_id:'r',kind:'Nutrition',version:1,status:'Active',availability:{usable:true,reason:null},content:{title:'Breakfast example',meals:[{name:'Breakfast',portions:[{food_id:'oats',grams:50}],preparation:'Prepare oats.'}],limitations:['General meal example.']},rationale:'A current meal example.'}]};
 else if(p==='/api/me/usage')data={account_used:24,account_limit:100,shared_hourly_limit:10,next_capacity_at:null,service_availability:'Busy',blocking_reason:'The studio is currently busy.'};
 else if(p==='/api/me/activity')data=[{id:'r',title:'Your training and meal update',status:'Ready for review',detail:'Review both drafts.',needs_user_action:true,action:'plans',created_at:'2026-10-24T00:00:00Z',stages:[],can_cancel:false}];
 else throw new Error(p);await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'My plans',exact:true}).click();
 await expect(page.getByText('Rolled oats',{exact:true})).toBeVisible();await expect(page.getByText('50 g',{exact:true})).toBeVisible();
 await expect(page.getByText('Food Id',{exact:true})).toHaveCount(0);
 await page.getByText('How to prepare',{exact:true}).click();await expect(page.getByText('Prepare oats.')).toBeVisible();
 await page.getByRole('button',{name:'Activity',exact:true}).click();
 await expect(page.getByText('24 of 100 calls',{exact:false})).toBeVisible();await expect(page.getByText('Ready for review',{exact:true})).toBeVisible();
 if(width<=390){const copy=await page.locator('.activity-copy').boundingBox();const action=await page.getByRole('button',{name:'Review plans',exact:false}).boundingBox();expect(copy!.width).toBeGreaterThan(170);expect(action!.y).toBeGreaterThanOrEqual(copy!.y+copy!.height-1);}
 await page.getByRole('button',{name:'In progress',exact:true}).click();await expect(page.getByText('You’re all caught up')).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);expect(errors).toEqual([]);
});

test('direct plan view and chosen handoff preserve message authors',async({page})=>{
 let role='Intake';const messages:any[]=[];const actions:string[]=[];
 await page.route('**/api/**',async route=>{const p=new URL(route.request().url()).pathname;let data:unknown;
 const conversation={id:'c',title:'Your fitness journey',active_role:role};
 if(p==='/api/auth/me')data={user:{id:'m',display_name:'Member',role:'Member'},csrf_token:'x'};
 else if(p==='/api/conversations')data=route.request().method()==='POST'?conversation:[];
 else if(p==='/api/conversations/c')data={conversation,messages,requests:[]};
 else if(p==='/api/me/plan-summary')data={current_version:0,plans:[],schedule_note:'No schedule.'};
 else if(p==='/api/conversations/c/messages'){const body=route.request().postDataJSON();actions.push(body.action);expect(body.purpose).toBe('Auto');if(body.action==='ConsultNutrition')role='Nutrition';messages.push({id:String(messages.length),sender:role,content:role==='Intake'?'Here are your current plans.':'What would you like to explore?',cards:body.action==='ViewPlans'?[{type:'PlanSummary'}]:[{type:'Handoff',role}]});data={request_id:'r'};}
 else throw new Error(p);await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'View my plans',exact:true}).click();
 await expect(page.getByText('You don’t have an active plan yet.')).toBeVisible();
 await page.getByRole('button',{name:'With Front Desk'}).click();
 await page.getByRole('button',{name:'Nutrition Assistant',exact:true}).click();
 await expect(page.getByText('You’re now chatting with Nutrition Assistant.')).toBeVisible();
 await expect(page.locator('.message-body>small').filter({hasText:'Front Desk'})).toHaveCount(1);
 await expect(page.getByRole('heading',{name:'Your fitness assistant',exact:true})).toBeVisible();
 expect(actions).toEqual(['ViewPlans','ConsultNutrition']);
});

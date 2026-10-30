import {test,expect} from '@playwright/test';
for(const changed of [false,true])test(`meal decision appears after concrete review: changed=${changed}`,async({page})=>{
 let action:any=null;
 await page.route('**/api/**',async route=>{const p=new URL(route.request().url()).pathname;let data:any=[];
 if(p==='/api/auth/me')data={user:{id:'u',role:'Member',display_name:'Member'},csrf_token:'x'};
 if(p==='/api/conversations')data=[{id:'c',title:'Training revision'}];
 if(p==='/api/conversations/c')data={conversation:{id:'c',title:'Training revision',active_role:'Coach'},messages:[{id:'m',sender:'Nutrition',content:changed?'Here are specific meal suggestions.':'Your meals can stay unchanged.',cards:[{type:'TrainingMealReview',pair_id:'pair',request_id:'r',meal_changes:changed}]}],requests:[{id:'r',kind:'Combined',status:'AwaitingConfirmation',result:{}}]};
 if(p==='/api/conversations/c/messages'){action=route.request().postDataJSON();data={request_id:'next',status:'Queued'};}
 if(p==='/api/me/plans')data={member_status:'Accepted',current:{version:1},plans:[],pairs:[],catalogue_labels:{}};
 await route.fulfill({json:data});});
 await page.goto('/#/assistant/c');
 await expect(page.getByRole('button',{name:'Review both plans',exact:true})).toHaveCount(0);
 const preserve=page.getByRole('button',{name:'Keep meals · Revise training'});
 if(changed){await preserve.click();await expect.poll(()=>action?.action).toBe('ReviseTrainingKeepMeals');expect(action.source_request_id).toBe('r');}
 else{await expect(preserve).toHaveCount(0);await page.getByRole('button',{name:'Review training · Meals unchanged'}).click();await expect(page.getByRole('dialog',{name:'Your plans'})).toBeVisible();}
});

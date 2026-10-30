import {test,expect} from '@playwright/test';
test('continuity reminder opens a chat preview without saving feedback',async({page})=>{
 const writes:string[]=[];let started=false;
 await page.route('**/api/**',async r=>{const path=new URL(r.request().url()).pathname;if(r.request().method()!=='GET')writes.push(path);let data:any=[];
 if(path==='/api/auth/me')data={user:{id:'member',display_name:'Member',role:'Member'},csrf_token:'x'};
 if(path==='/api/conversations')data=r.request().method()==='POST'?{id:'c',title:'Review',active_role:'Coach'}:[];
 if(path==='/api/me/notifications')data=[{id:'n',title:'How is your routine going?',kind:'Continuity',body:'Share your experience.',created_at:'2026-10-20T12:00:00',read_at:null}];
 if(path==='/api/conversations/c/member-actions'){started=true;data={};}
 if(path==='/api/conversations/c')data={conversation:{id:'c',title:'Review',active_role:'Coach'},messages:started?[{id:'m',sender:'Coach',content:'Review your feedback before saving.',cards:[{type:'MemberAction',kind:'Feedback',state:'Pending',revision:1,details:{plan_kind:'Training'},labels:{},plans:[]}]}]:[],requests:[]};
 await r.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'Updates',exact:true}).click();expect(writes).toEqual([]);await page.getByRole('button',{name:'Record training feedback'}).click();await expect(page.getByRole('heading',{name:'Your feedback'})).toBeVisible();await expect(page).toHaveURL(/#\/assistant\/c$/);expect(writes).toEqual(['/api/conversations','/api/conversations/c/member-actions']);await expect(page.getByRole('button',{name:'Save feedback',exact:true})).toBeDisabled();
});

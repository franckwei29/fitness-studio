import { test, expect } from '@playwright/test';

test.use({baseURL:process.env.RECOVERY_TEST_URL || 'http://127.0.0.1:5178'});

test('saved request updates from delay to completion without another member submission',async({page},testInfo)=>{
 let state:'Waiting'|'Queued'|'Running'|'Completed'='Waiting';
 let submissions=0;
 const conversation={id:'recovery',title:'Saved plan request',active_role:'Intake'};
 const detail='We’ve saved your request. Preparation is temporarily delayed while we restore service availability. It will continue automatically when available.';
 const errors:string[]=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  let data:unknown;
  if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if(path==='/api/auth/me')data={user:{id:'member',display_name:'Member',role:'Member'},csrf_token:'test'};
  else if(path==='/api/conversations')data=[conversation];
  else if(path.includes('/events')){await route.fulfill({status:200,contentType:'text/event-stream',body:': reconnect\n\n'});return;}
  else if(path==='/api/conversations/recovery')data={conversation,messages:[{id:'original',sender:'User',content:'Create training and meal drafts.'},...(state==='Completed'?[{id:'reply',sender:'Nutrition',content:'Your draft plans are ready.',cards:[{type:'ReviewDrafts'}]}]:[])],requests:[{id:'original-request',kind:'Combined',status:state,experience:{id:'original-request',title:'Your training and meal update',status:state==='Waiting'?'Temporarily delayed':state==='Completed'?'Ready to review':'Resuming your plans',detail:state==='Waiting'?detail:state==='Completed'?'Review your draft plans.':'Preparation is continuing from where it stopped. You don’t need to submit again.',stages:[{role:'Coach',done:false,running:state==='Running'}],no_action_needed:state!=='Completed',needs_user_action:state==='Completed',action:state==='Completed'?'plans':'conversation',can_cancel:state!=='Completed'}}]};
  else if(path==='/api/conversations/recovery/messages'){submissions++;throw Error('Recovery must not submit another member request');}
  else throw Error(`Unexpected API: ${path}`);
  await route.fulfill({json:data});
 });
 await page.goto('/#/assistant/recovery');
 await expect(page.getByText(detail,{exact:true})).toBeVisible();
 await expect(page.getByText('No action needed. We’ll update this conversation automatically.')).toBeVisible();
 await expect(page.locator('.task-stages')).toBeEmpty();
 await page.getByRole('textbox',{name:'Message',exact:true}).fill('My unsent question');
 await expect(page.getByRole('button',{name:'Send message'})).toBeEnabled();
 await page.screenshot({path:testInfo.outputPath('member-delayed.png'),fullPage:true});
 state='Queued';
 await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
 await expect(page.getByText('Resuming your plans',{exact:true})).toBeVisible();
 await expect(page.getByRole('textbox',{name:'Message',exact:true})).toHaveValue('My unsent question');
 state='Running';
 await page.evaluate(()=>window.dispatchEvent(new Event('online')));
 await expect(page.locator('.planning-task .spin')).toBeVisible();
 state='Completed';
 await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
 await expect(page.getByText('Your draft plans are ready.',{exact:true})).toBeVisible();
 await expect(page.getByText('Temporarily delayed',{exact:true})).toHaveCount(0);
 await expect(page.locator('.planning-task')).toHaveCount(1);
 await expect(page.getByRole('button',{name:'Review draft plans',exact:true})).toBeVisible();
 expect(submissions).toBe(0);
 expect(errors).toEqual([]);
 await page.screenshot({path:testInfo.outputPath('member-resumed.png'),fullPage:true});
});

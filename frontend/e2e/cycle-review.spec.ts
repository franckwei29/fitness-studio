import { test, expect } from '@playwright/test';

// Isolated UI fixtures verify confirmation and status wording, not model behavior.
test('member explicitly confirms cycle feedback and sees awaiting confirmation',async({page})=>{
 let submitted:any=null;
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;let data:unknown;
  if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if(path==='/api/auth/me')data={user:{id:'member',display_name:'Test Member',role:'Member'},csrf_token:'test'};
  else if(path==='/api/conversations')data=[];
  else if(path==='/api/me/plans')data={member_status:'Accepted',catalogue_labels:{},substitutions_enabled:false,current:{version:1,training_id:'training',nutrition_id:null,pair_id:null},pairs:[],plans:[{id:'training',request_id:'old',kind:'Training',version:1,status:'Active',availability:{usable:true,reason:null},content:{title:'Current routine'},rationale:'Fixture'}]};
  else if(path==='/api/me/cycle-reviews'){submitted=route.request().postDataJSON();data={request_id:'review'};}
  else if(path==='/api/requests/review')data={status:'AwaitingConfirmation'};
  else throw new Error(`Unexpected test request: ${path}`);
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'My plans',exact:true}).click();
 await page.getByRole('button',{name:'Review this training cycle'}).click();
 await expect(page.getByRole('combobox',{name:'Overall difficulty',exact:true})).toHaveValue('');
 await expect(page.getByRole('combobox',{name:'Did you experience discomfort?',exact:true})).toHaveValue('');
 await page.getByLabel('Period start').fill('2026-10-01');await page.getByLabel('Period end').fill('2026-10-07');
 await page.getByLabel('Sessions planned for this period').fill('3');await page.getByLabel('Sessions you actually completed').fill('2');
 await page.getByRole('combobox',{name:'Overall difficulty',exact:true}).selectOption('Manageable');
 await page.getByRole('combobox',{name:'Did you experience discomfort?',exact:true}).selectOption('no');
 await page.getByRole('combobox',{name:'Requested adjustment',exact:true}).selectOption('Maintenance');
 await page.getByLabel('What did you experience?').fill('I completed two sessions and missed one because of travel.');
 await page.getByRole('button',{name:'Request next-cycle candidate'}).click();expect(submitted).toBeNull();
 await page.getByRole('checkbox',{name:'I confirm this feedback describes my actual experience.'}).check();
 await page.getByRole('button',{name:'Request next-cycle candidate'}).click();
 await expect(page.getByText('Confirmed feedback saved and cycle review queued. Your active plan has not changed.')).toBeVisible();
 expect(submitted.feedback).toMatchObject({training_id:'training',completed_sessions:2,confirmed:true,discomfort:false});
 await page.getByRole('button',{name:'Check cycle review progress'}).click();
 await expect(page.getByText('Your next-cycle candidate is ready. Review and activate it below to complete the cycle review.')).toBeVisible();
 expect(errors).toEqual([]);
});

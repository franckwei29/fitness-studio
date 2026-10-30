import {test,expect,type Page} from '@playwright/test';
const facts={age:30,experience:'Beginner',available_days:2,session_minutes:30,goal:'Build consistency',equipment:['Dumbbells'],dietary_restrictions:[],prescribed_exercise:false,prescribed_nutrition:false,medical_supervision:false,pregnancy_postpartum:false,postoperative_acute_injury:false,resolved_past_injury:true};
async function setup(page:Page,status='PendingOwnerReview'){
  const state={assessment:{id:'intake',version:2,status,confirmed_facts:facts,proposed_facts:{age:20},decision:null},posts:0,reads:0,failRead:false,failSubmit:false};
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    let data:unknown;
    if(new URL(route.request().url()).pathname === '/api/owner/updates') { await route.fulfill({json:{needs_action:0,items:[]}}); return; }
  if(path==='/api/auth/me')data={user:{id:'member',display_name:'Demo Member',role:'Member'},csrf_token:'test'};
    else if(path==='/api/conversations')data=[];
    else if(path==='/api/me/profile'){
      state.reads++;if(state.failRead){await route.fulfill({status:503,json:{detail:'Temporarily unavailable'}});return;}
      data={member:{status:state.assessment.status==='Accepted'?'Accepted':'Suspended',profile_version:state.assessment.status==='Accepted'?2:1},snapshot:{facts},assessment:state.assessment};
    }else if(path==='/api/assessments/intake/confirm'){
      state.posts++;
      if(state.failSubmit){await route.fulfill({status:503,json:{detail:'Please try again.'}});return;}
      state.assessment={...state.assessment,status:'PendingOwnerReview',version:state.assessment.version+1,confirmed_facts:route.request().postDataJSON().facts};
      data=state.assessment;
    }else throw Error(`Unexpected request ${path}`);
    await route.fulfill({json:data});
  });
  await page.goto('/');await page.getByRole('button',{name:'My profile',exact:true}).click();
  await expect(page.getByLabel('Age',{exact:true})).toHaveValue('30');
  return state;
}
const receipt='Submitted — awaiting owner review';

test('pending receipt persists after reload and editing is explicit and cancellable',async({page})=>{
  const state=await setup(page);
  await expect(page.getByText(receipt,{exact:true})).toBeVisible();
  await expect(page.getByLabel('Age',{exact:true})).toBeDisabled();
  await expect(page.getByRole('button',{name:'Confirm my details',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Edit submitted details'}).click();
  await page.getByLabel('Age',{exact:true}).fill('31');
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect.poll(()=>state.reads).toBeGreaterThan(1);
  await expect(page.getByLabel('Age',{exact:true})).toHaveValue('31');
  await page.getByRole('button',{name:'Cancel editing'}).click();
  await expect(page.getByLabel('Age',{exact:true})).toHaveValue('30');
  await page.reload();await page.getByRole('button',{name:'My profile',exact:true}).click();
  await expect(page.getByText(receipt,{exact:true})).toBeVisible();expect(state.posts).toBe(0);
});

test('successful submission replaces Confirm with receipt beside the action',async({page})=>{
  const state=await setup(page,'Collecting');
  await page.getByRole('checkbox').check();
  await page.getByRole('button',{name:'Confirm my details',exact:true}).click();
  await expect(page.locator('.intake-submission').getByText(receipt,{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Confirm my details',exact:true})).toHaveCount(0);
  await expect(page.getByRole('checkbox')).toHaveCount(0);expect(state.posts).toBe(1);
  await page.getByRole('button',{name:'Edit submitted details'}).click();
  await page.getByLabel('Age',{exact:true}).fill('31');await page.getByRole('checkbox').check();
  await page.getByRole('button',{name:'Submit updated details'}).click();
  await expect(page.getByText(receipt,{exact:true})).toBeVisible();expect(state.posts).toBe(2);
  await expect(page.getByLabel('Age',{exact:true})).toHaveValue('31');
});

test('owner approval automatically updates member status and confirmed version',async({page})=>{
  const state=await setup(page);
  state.assessment.status='Accepted';
  await expect(page.getByText('Your confirmed profile is ready for planning.',{exact:true})).toBeVisible({timeout:10000});
  await expect(page.locator('.profile-status').getByText('Accepted',{exact:true})).toBeVisible();
  await expect(page.getByText('Confirmed profile version 2',{exact:true})).toBeVisible();
  await expect(page.getByText(receipt,{exact:true})).toHaveCount(0);expect(state.posts).toBe(0);
});

test('owner decision during editing blocks stale submission without discarding draft',async({page})=>{
  const state=await setup(page);
  await page.getByRole('button',{name:'Edit submitted details'}).click();
  await page.getByLabel('Age',{exact:true}).fill('31');state.assessment.status='Accepted';
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await expect(page.getByRole('alert')).toContainText('Your review status changed');
  await expect(page.getByLabel('Age',{exact:true})).toHaveValue('31');
  await expect(page.getByRole('button',{name:'Submit updated details'})).toHaveCount(0);
  await page.getByRole('button',{name:'Review latest details'}).click();
  await expect(page.getByText('Approved',{exact:true})).toBeVisible();expect(state.posts).toBe(0);
});

test('saved receipt survives a failed membership refresh',async({page})=>{
  const state=await setup(page,'Collecting');state.failRead=true;
  await page.getByRole('checkbox').check();await page.getByRole('button',{name:'Confirm my details',exact:true}).click();
  await expect(page.getByText(receipt,{exact:true})).toBeVisible();
  await expect(page.getByText('Your submission was saved. Membership status will refresh automatically.')).toBeVisible();
  await expect(page.getByRole('button',{name:'Confirm my details',exact:true})).toHaveCount(0);expect(state.posts).toBe(1);
});

test('submission failure keeps the draft and shows error beside Confirm',async({page})=>{
  const state=await setup(page,'Collecting');state.failSubmit=true;
  await page.getByLabel('Age',{exact:true}).fill('31');await page.getByRole('checkbox').check();
  await page.getByRole('button',{name:'Confirm my details',exact:true}).click();
  await expect(page.locator('.intake-submission').getByRole('alert')).toHaveText('Please try again.');
  await expect(page.getByLabel('Age',{exact:true})).toHaveValue('31');
  await expect(page.getByRole('button',{name:'Confirm my details',exact:true})).toBeEnabled();
});

import {test,expect} from '@playwright/test';
for(const width of [390,1440])test(`owner Updates retains seen work and opens a spending dialog ${width}`,async({page})=>{
 await page.setViewportSize({width,height:960});
 let seen=false,limit=1000000,saved=0;
 const item={id:'global',category:'Spending',title:'Whole studio',status:'Needs attention',description:'2 requests are paused.',target:{page:'budgets',id:'global'},action:'budget'};
 await page.route('**/api/**',async route=>{const path=new URL(route.request().url()).pathname;let data:any=[];
 if(path==='/api/auth/me')data={user:{id:'o',display_name:'Owner',role:'Owner'},csrf_token:'x'};
 else if(path==='/api/owner/updates')data={needs_action:saved?0:1,items:[{id:'notice',state:saved?'Resolved':'Needs action',read_at:seen?'2026-10-30T01:00:00':null,created_at:'2026-10-30T00:00:00',item}]};
 else if(path==='/api/owner/updates/notice/read'){seen=true;data={read:true};}
 else if(path==='/api/owner/budgets'){if(route.request().method()==='POST'){limit=route.request().postDataJSON().limit_micros;saved++;data={id:'b',scope_key:'global',limit_micros:limit,fingerprint:'v2'};}else data=[{id:'b',scope_key:'global',limit_micros:limit,spent_micros:10,reserved_micros:20,fingerprint:'v1'}];}
 await route.fulfill({json:data});});
 await page.goto('/#/owner/notifications');await expect(page.locator('.updates-count')).toHaveText('1');await page.getByRole('button',{name:'Dismiss notification'}).click();await page.getByRole('button',{name:'Mark as read',exact:true}).click();await expect(page.locator('.updates-count')).toHaveText('1');await page.getByRole('button',{name:'Adjust budget'}).click();const dialog=page.getByRole('dialog');await expect(dialog).toBeVisible();await dialog.getByLabel('Total limit (USD)').fill('50');await dialog.getByLabel('Reason',{exact:true}).fill('Acceptance budget adjustment');await dialog.getByRole('button',{name:'Confirm budget change'}).click();await expect(page.locator('.updates-count')).toHaveCount(0);await page.getByRole('button',{name:/Resolved 1/}).click();await expect(page.getByText('This item no longer needs action.',{exact:false})).toBeVisible();expect(saved).toBe(1);expect(limit).toBe(50000000);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('uncertain usage can be acknowledged without settling its cost',async({page})=>{
 let acknowledged=false;
 await page.route('**/api/**',async route=>{const path=new URL(route.request().url()).pathname;let data:any=[];
 if(path==='/api/auth/me')data={user:{id:'o',display_name:'Owner',role:'Owner'},csrf_token:'x'};
 if(path==='/api/owner/updates')data={needs_action:acknowledged?0:1,items:[{id:'notice',state:acknowledged?'Monitoring':'Needs action',read_at:null,created_at:'2026-10-30T00:00:00',item:{id:'call',category:'Usage',title:'Nutrition · Usage review',status:'Cost unconfirmed',description:'$0.478413 remains reserved.',target:{page:'budgets',id:'call',section:'unknown-usage'},action:acknowledged?undefined:'acknowledge-usage'}}]};
 if(path==='/api/owner/unknown-usage/call/acknowledge'){acknowledged=true;data={status:'Monitoring',reservation_retained_micros:478413};}
 await route.fulfill({json:data});});
 await page.goto('/#/owner/notifications');await page.getByRole('button',{name:'Acknowledge & archive'}).click();
 await expect(page.getByRole('dialog')).toContainText('Reserved funds remain');
 await page.getByRole('button',{name:'Confirm acknowledgement'}).click();
 await expect(page.locator('.updates-count')).toHaveCount(0);
 await page.reload();await page.getByRole('button',{name:'Monitoring 1'}).click();
 await expect(page.getByText('$0.478413 remains reserved.',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Acknowledge & archive'})).toHaveCount(0);
});

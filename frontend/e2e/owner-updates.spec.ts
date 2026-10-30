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

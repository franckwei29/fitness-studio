import {test,expect} from '@playwright/test';
test('rename and delete conversation with confirmation and persistence',async({page})=>{
 let rows=[{id:'c',title:'Your fitness journey',active_role:'Intake'}];
 await page.route('**/api/**',async route=>{const path=new URL(route.request().url()).pathname;let data:any=[];
 if(path==='/api/auth/me')data={user:{id:'u',role:'Member',display_name:'Member'},csrf_token:'x'};
 if(path==='/api/conversations')data=rows;
 if(path==='/api/conversations/c')data={conversation:rows[0],messages:[],requests:[]};
 if(path.endsWith('/rename')){rows[0].title=route.request().postDataJSON().title;data=rows[0];}
 if(path.endsWith('/delete')){rows=[];data={deleted:true};}
 await route.fulfill({json:data});});
 await page.goto('/#/assistant/c');
 await page.getByRole('button',{name:'Options for Your fitness journey'}).click();
 await page.getByRole('button',{name:'Rename',exact:true}).click();
 await page.getByLabel('Conversation name').fill('My training questions');
 await page.getByRole('button',{name:'Save name'}).click();
 await expect(page.getByRole('button',{name:'Options for My training questions'})).toBeVisible();
 await page.reload();await page.getByRole('button',{name:'Options for My training questions'}).click();
 await page.getByRole('button',{name:'Delete',exact:true}).click();
 await expect(page.getByRole('dialog')).toContainText('Saved plans, profile details');
 await page.getByRole('button',{name:'Cancel',exact:true}).click();expect(rows).toHaveLength(1);
 await page.getByRole('button',{name:'Options for My training questions'}).click();await page.getByRole('button',{name:'Delete',exact:true}).click();
 await page.getByRole('button',{name:'Delete conversation',exact:true}).click();
 await expect(page.locator('.conversation-row')).toHaveCount(0);
 await expect(page).toHaveURL(/#\/assistant$/);
 await page.reload();await expect(page.locator('.conversation-row')).toHaveCount(0);
});

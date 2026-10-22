import { test, expect } from '@playwright/test';

test('owner records both configuration and release prerequisites', async ({ page }) => {
 const incident={id:'incident',title:'Task needs review',status:'Open',outcome:'Unresolved',current_fence:3,task_status:'Waiting',evidence:{role:'Coach',reason:'PolicyReview',required_changes:[] as {id:string;kind:string}[]}};
 let saved:unknown;
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  let data:unknown=[];
  if(path==='/api/auth/me')data={user:{id:'owner',display_name:'Owner',role:'Owner'},csrf_token:'test'};
  else if(path==='/api/owner/incidents')data=[incident];
  else if(path==='/api/owner/changes')data=[{id:'config',kind:'GroupConfiguration',reason:'Enable required capability',status:'Executed'}];
  else if(path==='/api/owner/releases')data=[{id:'release',kind:'AgentRelease',reason:'Restore reviewed model version',status:'Canary'}];
  else if(path==='/api/owner/incidents/incident/prerequisites'){
   saved=route.request().postDataJSON();incident.evidence.required_changes=[{id:'config',kind:'GroupConfiguration'},{id:'release',kind:'AgentRelease'}];data=incident;
  }
  await route.fulfill({json:data});
 });
 await page.goto('/');
 await page.getByRole('button',{name:'Incidents',exact:true}).click();
 await page.getByRole('button',{name:/Task needs review/}).click();
 await page.getByRole('button',{name:'Set required recovery changes'}).click();
 await page.getByLabel(/Enable required capability/).check();
 await page.getByLabel(/Restore reviewed model version/).check();
 await page.getByRole('button',{name:'Save recovery prerequisites'}).click();
 await expect(page.getByText('Required changes recorded: 2')).toBeVisible();
 expect(saved).toEqual({expected_fence:3,change_ids:['config','release']});
});

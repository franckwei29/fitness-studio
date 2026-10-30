import {test,expect} from '@playwright/test';
// Fixture results exercise the real comparison component and shared control CSS.
for(const width of [320,390,1440])for(const theme of ['light','dark'] as const){
 test(`decision controls have visible boundaries and spacing ${width}px ${theme}`,async({page})=>{
 await page.setViewportSize({width,height:900});await page.emulateMedia({colorScheme:theme});
 await page.route('**/api/**',async route=>{
 const path=new URL(route.request().url()).pathname;let data:unknown;
 if(path==='/api/auth/me')data={user:{id:'owner',display_name:'Owner',role:'Owner'},csrf_token:'fixture'};
 else if(path==='/api/owner/updates')data={needs_action:1,items:[]};
 else if(path==='/api/conversations'||path==='/api/owner/groups')data=[];
 else if(path==='/api/owner/agent-versions')data={default_model:'gpt-6-luna',versions:[{id:'candidate',role:'Coach',label:'Gemini trial',model:'gemini-3.5-flash-lite',prompt:'Fixed test instructions',configured_groups:[]}]};
 else if(path.endsWith('/evaluations/run'))data={decision_fingerprint:'test',release:null,run:{id:'run',baseline_id:'current',candidate_id:'candidate',status:'Completed',evidence_mode:'Live',cases:[],suite_version:'fixture'},models:[],results:[],tasks:[],usage:{calls:0,settled_micros:0,unknown_calls:0}};
 else throw Error('Unexpected fixture '+path);
 await route.fulfill({json:data});});
 await page.goto('/#/owner/versions/candidate?section=evaluation:run');
 const keep=page.getByRole('button',{name:'Keep current model',exact:true});const trial=page.getByRole('button',{name:'Prepare Canary release',exact:true});
 await expect(keep).toBeVisible();await expect(trial).toBeVisible();
 const a=(await keep.boundingBox())!,b=(await trial.boundingBox())!;
 const horizontal=b.x-a.x-a.width;const vertical=b.y-a.y-a.height;
 expect(Math.max(horizontal,vertical)).toBeGreaterThanOrEqual(9);
 for(const button of [keep,trial]){
  const style=await button.evaluate(el=>{const s=getComputedStyle(el);return {border:parseFloat(s.borderTopWidth),height:el.getBoundingClientRect().height,radius:parseFloat(s.borderRadius)};});
  expect(style.border).toBeGreaterThanOrEqual(1);expect(style.height).toBeGreaterThanOrEqual(44);expect(style.radius).toBeGreaterThanOrEqual(8);
 }
 await trial.focus();expect(await trial.evaluate(el=>parseFloat(getComputedStyle(el).outlineWidth))).toBeGreaterThanOrEqual(3);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
 await page.locator('.comparison-decision').screenshot({path:`../.local/controls-${width}-${theme}.png`});
 });
}

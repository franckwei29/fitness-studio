import { test, expect } from '@playwright/test';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';

test('SSE reconnect resumes its cursor and late snapshots cannot regress completion',async({page})=>{
  const conversation={id:'conversation',title:'Event replay',active_role:'Coach'};
  let streams=0,reads=0,reconnectCursor:string|undefined;
  let releaseOld:()=>void=()=>{};
  let oldReturned=false;
  const oldGate=new Promise<void>(resolve=>{releaseOld=resolve;});
  const server=createServer((request,response)=>{
    streams++;
    response.writeHead(200,{'Content-Type':'text/event-stream','Access-Control-Allow-Origin':'*','Cache-Control':'no-cache'});
    if(streams===1)response.end('retry: 300\n\nid: 1\nevent: processing\ndata: {"status":"Running"}\n\n');
    else{
      reconnectCursor=request.headers['last-event-id'] as string|undefined;
      response.end('id: 1\nevent: queued\ndata: {"status":"Queued"}\n\nid: 2\nevent: completed\ndata: {"status":"Completed"}\n\n');
    }
  });
  await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
  const streamUrl=`http://127.0.0.1:${(server.address() as AddressInfo).port}/events`;
  await page.addInitScript(url=>{
    const NativeEventSource=window.EventSource;
    window.EventSource=class extends NativeEventSource{
      constructor(_url:string|URL,options?:EventSourceInit){super(url,options);}
    };
  },streamUrl);
  try{
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/auth/me'){await route.fulfill({json:{user:{id:'member',display_name:'Demo Member',role:'Member'},csrf_token:'test'}});return;}
    if(path==='/api/conversations'){await route.fulfill({json:[conversation]});return;}
    if(path==='/api/conversations/conversation'){
      const read=++reads;
      if(read===2)await oldGate;
      const completed=read>=3;
      await route.fulfill({json:{conversation,event_cursor:completed?2:read===2?1:0,
        messages:completed?[{id:'reply',sender:'Coach',content:'Your latest answer is ready.'}]:[],
        requests:[{id:'request',kind:'Chat',status:completed?'Completed':read===2?'Running':'Queued'}]}});
      if(read===2)oldReturned=true;
      return;
    }
    throw Error(`Unexpected request: ${path}`);
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Event replay',exact:true}).click();
  await expect(page.getByText('Your latest answer is ready.')).toBeVisible();
  expect(reconnectCursor).toBe('1');
  releaseOld();
  await expect.poll(()=>oldReturned).toBe(true);
  await expect(page.getByText('Your latest answer is ready.')).toBeVisible();
  await expect(page.getByText('Working on your request…')).toHaveCount(0);
  expect(reads).toBe(3);
  }finally{releaseOld();server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
});

/** Durable SSE replay. EventSource reconnects with Last-Event-ID after each batch. */
export const requestEventTypes = [
  'workflow_changed', 'queued', 'processing', 'waiting', 'stopped', 'recovery', 'recovery_queued',
  'training_candidate', 'nutrition_candidate', 'completed', 'evaluation_case_completed',
  'plan_reviewed', 'policy_changed', 'correction_queued', 'expired', 'cancelled',
];

export function watchRequest(id:string, changed:()=>void, after=0){
  const source=new EventSource(`/api/requests/${encodeURIComponent(id)}/events?after=${after}`);
  let cursor=after;
  const receive=(raw:Event)=>{
    const message=raw as MessageEvent<string>;
    const sequence=Number(message.lastEventId);
    if(!Number.isSafeInteger(sequence)||sequence<=cursor)return;
    let payload:{status?:string};
    try{payload=JSON.parse(message.data);}catch{return;}
    if(!payload||typeof payload!=='object')return;
    cursor=sequence;
    // Events invalidate a snapshot; never apply historical status to current UI.
    changed();
    if(['Completed','Stopped','Cancelled'].includes(payload.status||''))source.close();
  };
  for(const type of requestEventTypes)source.addEventListener(type,receive);
  return ()=>source.close();
}

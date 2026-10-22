import { useEffect, useState } from 'react';
import { api } from './api';

type RequestRow = {id:string;kind:string;status:string;status_message?:string;created_at:string};
const cancellable = new Set(['Queued','Running','Waiting','AwaitingConfirmation']);

export function Requests(){
  const [rows,setRows]=useState<RequestRow[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState('');
  async function load(){setRows(await api<RequestRow[]>('/requests'));}
  useEffect(()=>{load().catch(e=>setError(e.message));},[]);
  async function cancel(id:string){
    setBusy(id);setError('');
    try{await api(`/requests/${id}/cancel`,{});await load();}
    catch(e){setError((e as Error).message);}finally{setBusy('');}
  }
  return <section className="intake-page"><span className="eyebrow">YOUR ACTIVITY</span><h1>Requests</h1>
    <p className="muted">Your latest 100 requests across conversations, plan changes, and cycle reviews. Cancelling stops further work. Calls already dispatched may still incur usage; their results will not be applied.</p>
    <button className="text-button" onClick={()=>load().catch(e=>setError(e.message))}>Refresh requests</button>
    {error&&<p role="alert" className="error">{error}</p>}
    {!rows.length&&<p>No requests yet.</p>}
    {rows.map(row=><article className="plan-card" key={row.id}><h2>{row.kind} · {row.status}</h2>
      <p className="muted">Submitted {new Date(row.created_at.endsWith('Z')?row.created_at:`${row.created_at}Z`).toLocaleString()}</p>
      {row.status_message&&<p role="status">{row.status_message}</p>}
      {cancellable.has(row.status)&&<button className="text-button" disabled={!!busy} onClick={()=>cancel(row.id)}>{busy===row.id?'Cancelling…':'Cancel request'}</button>}
    </article>)}
  </section>;
}

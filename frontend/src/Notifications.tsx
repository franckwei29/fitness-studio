import { useEffect, useState } from 'react';
import { api } from './api';
type Notice={id:string;title:string;body:string;kind:string;read_at:string|null;created_at:string};
export function Notifications(){
 const [rows,setRows]=useState<Notice[]>([]),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 async function load(){setRows(await api<Notice[]>('/me/notifications'));}
 useEffect(()=>{load().catch(e=>setError(e.message)).finally(()=>setLoading(false));},[]);
 async function read(id:string){try{await api(`/me/notifications/${id}/read`,{});await load();}catch(e){setError((e as Error).message);}}
 return <section className="intake-page"><span className="eyebrow">STAY CONNECTED</span><h1>Your updates</h1><p className="muted">Profile reminders and check-ins based on your recorded activity.</p>{error&&<p role="alert" className="error">{error}</p>}{loading?<p>Loading updates…</p>:!rows.length?<div className="profile-status">You are all caught up. New updates will appear here.</div>:rows.map(row=><article className="plan-card" key={row.id}><span className="eyebrow">{row.read_at?'READ':'NEW'} · {new Date(`${row.created_at}Z`).toLocaleDateString('en-AU')}</span><h2>{row.title}</h2><p>{row.body}</p>{!row.read_at&&<button className="text-button" onClick={()=>read(row.id)}>Mark as read</button>}</article>)}</section>;
}

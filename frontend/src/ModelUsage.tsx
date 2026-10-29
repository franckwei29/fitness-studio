import { useEffect, useState } from 'react';
import { Cpu, KeyRound } from 'lucide-react';
import { api } from './api';

type ModelUsage = {id:string;label:string;provider:string;configured:boolean;used:number;limit:number;next_capacity_at:string|null};
export function ModelUsage(){
 const [data,setData]=useState<{model_usage?:ModelUsage[];default_model?:string}|null>(null),[error,setError]=useState('');
 useEffect(()=>{let live=true;const load=()=>api<{model_usage?:ModelUsage[];default_model?:string}>('/me/usage').then(value=>{if(live){setData(value);setError('');}}).catch(e=>{if(live)setError(e.message);});void load();const timer=setInterval(load,20000);return()=>{live=false;clearInterval(timer);};},[]);
 return <section className="model-usage-panel"><h2><Cpu size={22}/> AI model usage</h2><p className="muted">Each model has its own rolling hourly allowance. Each ordinary account shares one daily allowance across providers. Your studio budget is separate from your provider account balance.</p>{error&&<p role="status">{error}</p>}<div className="model-provider-grid">{data?.model_usage?.map(row=><article className="model-provider-card" key={`${row.provider}:${row.id}`}><div className="section-heading"><strong>{row.label}</strong>{row.id===data.default_model&&<span className="status-pill">Default</span>}</div><span><KeyRound size={14}/> {row.configured?'Configured':'Not configured'}</span><p><strong>{row.used} / {row.limit}</strong> calls · Last hour</p><progress aria-label={`${row.label} hourly usage`} value={Math.min(row.used,row.limit)} max={row.limit}/>{row.next_capacity_at&&<small>Next capacity returns: {new Date(row.next_capacity_at).toLocaleString()}</small>}</article>)}</div></section>;
}

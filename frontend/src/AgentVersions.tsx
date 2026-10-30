import { CurrentModelComparison } from './CurrentModelComparison';
import type { OwnerTarget } from './ownerNavigation';
import { useEffect, useState, type FormEvent } from 'react';
import { Cpu, KeyRound, SlidersHorizontal } from 'lucide-react';
import { api } from './api';
import { Evaluations } from './Evaluations';

type Version = {id:string;role:string;label:string;model:string;provider?:string;max_output_tokens?:number;reasoning_effort?:string|null;prompt:string;fingerprint:string;configured_groups:{id:string;name:string}[]};
type Model = {id:string;provider:string;label:string;configured:boolean;reasoning:string[];input_per_million:string;output_per_million:string};
type Listing = {default_model:string;default_provider?:string;models?:Model[];versions:Version[]};

export function AgentVersions({targetId,targetSection,onChanged}:{targetId?:string;targetSection?:string;onChanged?:(target?:OwnerTarget)=>void}) {
 const [data,setData]=useState<Listing|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const [loading,setLoading]=useState(true),[missing,setMissing]=useState(false);
 const [provider,setProvider]=useState(''),[model,setModel]=useState('');
 async function load(){const next=await api<Listing>('/owner/agent-versions');if(targetId&&!next.versions.some(row=>row.id===targetId))next.versions.unshift(await api<Version>(`/owner/agent-versions/${encodeURIComponent(targetId)}`));setData(next);}
 useEffect(()=>{let active=true;setLoading(true);setData(null);setMissing(false);setError('');api<Listing>('/owner/agent-versions').then(async next=>{if(targetId&&!next.versions.some(row=>row.id===targetId))next.versions.unshift(await api<Version>(`/owner/agent-versions/${encodeURIComponent(targetId)}`));if(active){setData(next);setProvider(next.default_provider||'gemini');setModel(next.default_model);}}).catch(e=>{if(active){setError(e.message);setMissing(!!targetId);}}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[targetId]);
 const models=data?.models||[];const selected=models.find(row=>row.id===model);
 async function create(event:FormEvent<HTMLFormElement>){
  event.preventDefault();if(busy)return;const form=event.currentTarget;const values=Object.fromEntries(new FormData(form));setBusy(true);setError('');setNotice('');
  try{const saved=await api<Version>('/owner/agent-versions',{...values,model,provider,max_output_tokens:Number(values.max_output_tokens||2048),reasoning_effort:values.reasoning_effort||null});form.reset();onChanged?.({page:'versions',id:saved.id});setNotice('Candidate saved. Evaluation and approval are required before release.');await load();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 return <section className="intake-page"><span className="eyebrow">AI CONFIGURATION</span><h1>Agent versions</h1><p className="muted">Choose the AI provider and model for each specialist. Save a candidate, evaluate it, then release it to a member group.</p>
 {error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status" className="assessment-status">{notice}</p>}
 {loading?<p role="status">Loading agent versions…</p>:missing?<p role="alert" className="error">This agent version is unavailable. No other version has been opened.</p>:data?<>
 {!targetId&&<CurrentModelComparison models={models} onChanged={onChanged}/>}
 {!targetId&&<form className="intake-form model-configuration" onSubmit={create}><h2><SlidersHorizontal size={22}/> New candidate</h2>
 <div className="model-provider-grid">{['openai','gemini'].map(name=><div className={`model-provider-card ${provider===name?'selected':''}`} key={name}><Cpu size={22}/><strong>{name==='openai'?'OpenAI':'Google Gemini'}</strong><span><KeyRound size={14}/> {models.some(row=>row.provider===name&&row.configured)?'Configured':'Not configured'}</span></div>)}</div>
 <div className="model-field-grid"><label>Provider<select aria-label="Provider" name="provider" value={provider} onChange={e=>{setProvider(e.target.value);setModel(models.find(row=>row.provider===e.target.value)?.id||'');}}><option value="openai">OpenAI</option><option value="gemini">Google Gemini</option></select></label>
 <label>Model{models.length?<select aria-label="Model" name="model" value={model} onChange={e=>setModel(e.target.value)}>{models.filter(row=>row.provider===provider).map(row=><option key={row.id} value={row.id}>{row.label}</option>)}</select>:<input name="model" value={model} onChange={e=>setModel(e.target.value)} required/>}</label></div>
 {selected&&<p className="muted">Standard text pricing per 1M tokens: ${selected.input_per_million} input · ${selected.output_per_million} output. {selected.configured?'Credentials are configured on the server.':'Add this provider’s credentials to the server before running an evaluation.'}</p>}
 <div className="model-field-grid"><label>Agent role<select name="role">{['Intake','Coach','Nutrition','TechnicalManager'].map(role=><option key={role} value={role}>{role==='TechnicalManager'?'Technical Manager':role}</option>)}</select></label><label>Version label<input name="label" required maxLength={80} placeholder="Clearer training explanations"/></label>
 <label>Response limit (tokens)<input name="max_output_tokens" type="number" defaultValue={2048} min={64} max={4096} required/></label>
 {!!selected?.reasoning.length&&<label>Reasoning effort<select aria-label="Reasoning effort" key={model} name="reasoning_effort" defaultValue="low">{selected.reasoning.map(effort=><option key={effort} value={effort}>{effort.charAt(0).toUpperCase()+effort.slice(1)}</option>)}</select></label>}</div>
 <label>Additional instructions<textarea name="instructions" required maxLength={12000} rows={5} placeholder="Describe the specific behavior to evaluate in this candidate."/></label><p className="muted">Core role and English-output instructions are included automatically. Saving does not call the model or deploy the candidate. API keys stay on the server.</p><button className="primary" disabled={busy}>{busy?'Saving…':'Save candidate'}</button></form>}
 <h2>{targetId?'Selected version':'Saved versions'}</h2>{!data.versions.length&&<p className="muted">No versions have been saved yet.</p>}{data.versions.filter(version=>!targetId||version.id===targetId).map(version=><details className="plan-card" key={version.id} open={!!targetId}><summary>{version.label} · {version.role} · {version.model}</summary><p className="status-pill"><Cpu size={14}/> {version.provider==='openai'?'OpenAI':'Google Gemini'} · Response limit {version.max_output_tokens||2048}{version.reasoning_effort?` · ${version.reasoning_effort} reasoning`:''}</p><p>{version.configured_groups.length?`Explicitly configured for: ${version.configured_groups.map(group=>group.name).join(', ')}`:'No explicit group assignment. This list includes initial runtime versions and unreleased candidates.'}</p><p className="muted">Version reference: {version.id}</p><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{version.prompt}</pre><Evaluations candidate={version} versions={data.versions} targetId={targetSection?.startsWith('evaluation:')?targetSection.slice(11):undefined} onChanged={onChanged}/></details>)}</>:<p className="muted">Loading agent versions…</p>}</section>;
}

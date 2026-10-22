import { useEffect, useState, type FormEvent } from 'react';
import { api } from './api';
import { Evaluations } from './Evaluations';

type Version = {id:string;role:string;label:string;model:string;prompt:string;fingerprint:string;configured_groups:{id:string;name:string}[]};
type Listing = {default_model:string;versions:Version[]};

export function AgentVersions(){
 const [data,setData]=useState<Listing|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 async function load(){setData(await api<Listing>('/owner/agent-versions'));}
 useEffect(()=>{load().catch(e=>setError(e.message));},[]);
 async function create(event:FormEvent<HTMLFormElement>){
  event.preventDefault();const form=event.currentTarget;const values=new FormData(form);setBusy(true);setError('');setNotice('');
  try{await api('/owner/agent-versions',Object.fromEntries(values));await load();form.reset();setNotice('Candidate saved. No live group configuration was changed. Evaluation and approval are required before release.');}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 return <section className="intake-page"><span className="eyebrow">CONTROLLED MODEL CHANGES</span><h1>Agent versions</h1><p className="muted">Save a separate candidate to refine an agent's instructions. Existing versions remain unchanged so tasks and plan history keep their original references.</p>{error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status" className="assessment-status">{notice}</p>}{data?<><form className="intake-form" onSubmit={create}><h2>New candidate</h2><label>Agent role<select name="role">{['Intake','Coach','Nutrition','TechnicalManager'].map(role=><option key={role} value={role}>{role==='TechnicalManager'?'Technical Manager':role}</option>)}</select></label><label>Version label<input name="label" required maxLength={80} placeholder="Clearer training explanations"/></label><label>Model<input name="model" required defaultValue={data.default_model} maxLength={100}/></label><label>Additional instructions<textarea name="instructions" required maxLength={12000} rows={5} placeholder="Describe the specific behavior to evaluate in this candidate."/></label><p className="muted">Core role and English-output instructions are included automatically. Saving does not call the model or deploy the candidate.</p><button className="primary" disabled={busy}>{busy?'Saving…':'Save candidate'}</button></form><h2>Saved versions</h2>{!data.versions.length&&<p className="muted">No versions have been saved yet.</p>}{data.versions.map(version=><details className="plan-card" key={version.id}><summary>{version.label} · {version.role} · {version.model}</summary><p>{version.configured_groups.length?`Explicitly configured for: ${version.configured_groups.map(group=>group.name).join(', ')}`:'No explicit group assignment. This list includes initial runtime versions and unreleased candidates.'}</p><p className="muted">Version reference: {version.id}</p><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{version.prompt}</pre><Evaluations candidate={version} versions={data.versions}/></details>)}</>:<p className="muted">Loading agent versions…</p>}</section>;
}

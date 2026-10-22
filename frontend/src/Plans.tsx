import { useEffect, useState } from 'react';
import { api } from './api';
import { Feedback } from './Feedback';
import { FoodSubstitution } from './FoodSubstitution';
import { CycleReview } from './CycleReview';
import { PlanChanges } from './PlanChanges';

type Plan={availability:{usable:boolean;reason:string|null};id:string;request_id:string;training_input_id?:string|null;baseline_id?:string|null;kind:string;version:number;status:string;content:Record<string,unknown>;rationale:string};
type Pair={id:string;training_id:string;nutrition_id:string;status:string};
type PlansData={catalogue_labels:Record<string,string>;substitutions_enabled:boolean;member_status:string|null;current:null|{version:number;training_id:string|null;nutrition_id:string|null;pair_id:string|null};plans:Plan[];pairs:Pair[]};
function Content({value,labels={}}:{value:unknown;labels?:Record<string,string>}){
 if(value===null||value===undefined)return <span>Not specified</span>;
 if(Array.isArray(value))return <ul>{value.map((item,index)=><li key={index}><Content value={item} labels={labels}/></li>)}</ul>;
 if(typeof value==='object')return <dl>{Object.entries(value as Record<string,unknown>).map(([key,item])=><div key={key}><dt>{key.replaceAll('_',' ')}</dt><dd><Content value={item} labels={labels}/></dd></div>)}</dl>;
 return <span>{typeof value==='boolean'?(value?'Yes':'No'):labels[String(value)]||String(value)}</span>;
}
export function Plans(){
 const [data,setData]=useState<PlansData|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(''),[notice,setNotice]=useState('');
 async function load(){setData(await api<PlansData>('/me/plans'));}
 useEffect(()=>{load().catch(e=>setError(e.message));},[]);
 async function confirm(pair:Pair){
  const plan=data?.plans.find(row=>row.id===pair.nutrition_id);if(!data?.current||!plan)return;
  setBusy(pair.id);setError('');setNotice('');
  try{await api(`/me/plans/pairs/${pair.id}/confirm`,{request_id:plan.request_id,expected_current_version:data.current.version,operation_key:crypto.randomUUID()});await load();setNotice('Your training and nutrition plans are now active.');}
  catch(e){setError((e as Error).message);await load();}finally{setBusy('');}
 }
 async function confirmStandalone(plan:Plan){
  if(!data?.current)return;setBusy(plan.id);setError('');setNotice('');
  try{await api(`/me/plans/${plan.kind.toLowerCase()}/${plan.id}/confirm`,{expected_current_version:data.current.version,operation_key:crypto.randomUUID()});await load();setNotice(`Your ${plan.kind.toLowerCase()} plan is now active.`);}catch(e){setError((e as Error).message);await load();}finally{setBusy('');}
 }
 const activeIds=[data?.current?.training_id,data?.current?.nutrition_id];
 const active=data?.plans.filter(plan=>activeIds.includes(plan.id))||[];
 const candidates=data?.pairs.filter(pair=>pair.status==='Candidate')||[];
 const standalone=data?.plans.filter(plan=>plan.status==='Validated'&&!activeIds.includes(plan.id)&&!data.pairs.some(pair=>pair.training_id===plan.id||pair.nutrition_id===plan.id)&&(plan.kind==='Training'||plan.training_input_id==null))||[];
 return <section className="intake-page"><span className="eyebrow">ONE CONNECTED ROUTINE</span><h1>Your plans</h1><p className="muted">Review individual training or nutrition plans, or a compatible plan pair. Candidates take effect only after confirmation and required checks.</p>{error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status" className="assessment-status">{notice}</p>}{!data?<p className="muted">Loading your plans…</p>:<>{data.member_status!=='Accepted'&&<p className="assessment-status">Complete or resolve your intake before activating a plan.</p>}<h2>Latest activated plans</h2>{!active.length?<div className="profile-status">You do not have an active plan yet. Once your intake is accepted, request training, nutrition, or both in your conversation.</div>:active.map(plan=><article className="plan-card" key={plan.id}><span className="eyebrow">{plan.kind} · VERSION {plan.version}</span><h3>{String(plan.content.title||`${plan.kind} plan`)}</h3><p className="muted">{plan.rationale}</p><PlanChanges plan={plan} plans={data.plans} labels={data.catalogue_labels}/><Content value={plan.content} labels={data.catalogue_labels}/>{!plan.availability.usable&&<p role="status" className="assessment-status">{plan.availability.reason}</p>}<Feedback planId={plan.id} kind={plan.kind}/>{plan.kind==='Training'&&plan.availability.usable&&data.current&&<CycleReview planId={plan.id} currentVersion={data.current.version} onReady={load}/>}{plan.kind==='Nutrition'&&plan.availability.usable&&data.substitutions_enabled&&data.current&&<FoodSubstitution planId={plan.id} currentVersion={data.current.version} meals={plan.content.meals as {name:string;portions:{food_id:string;grams:string|number}[]}[]} labels={data.catalogue_labels} onReady={load}/>}</article>)}<h2>Ready for review</h2>{!candidates.length&&!standalone.length&&<p className="muted">No candidate plans are waiting for confirmation.</p>}{candidates.map(pair=><article className="plan-card" key={pair.id}>{[pair.training_id,pair.nutrition_id].map(id=>{const plan=data.plans.find(row=>row.id===id);return plan?<section key={id}><h3>{plan.kind} · Version {plan.version}</h3><p className="muted">{plan.rationale}</p><PlanChanges plan={plan} plans={data.plans} labels={data.catalogue_labels}/><Content value={plan.content} labels={data.catalogue_labels}/></section>:null;})}<button className="primary" disabled={!!busy||data.member_status!=='Accepted'||[pair.training_id,pair.nutrition_id].some(id=>!data.plans.find(plan=>plan.id===id)?.availability.usable)} onClick={()=>confirm(pair)}>{busy===pair.id?'Checking and activating…':'Confirm this plan pair'}</button></article>)}{standalone.map(plan=><article className="plan-card" key={plan.id}><h3>{plan.kind} · Version {plan.version}</h3><p>{plan.rationale}</p><PlanChanges plan={plan} plans={data.plans} labels={data.catalogue_labels}/><Content value={plan.content} labels={data.catalogue_labels}/>{!plan.availability.usable&&<p className="assessment-status">{plan.availability.reason}</p>}<button className="primary" disabled={!!busy||!plan.availability.usable||data.member_status!=='Accepted'} onClick={()=>confirmStandalone(plan)}>Confirm standalone {plan.kind.toLowerCase()}</button></article>)}<h2>Version history</h2>{data.plans.filter(plan=>!activeIds.includes(plan.id)).map(plan=><details className="plan-card" key={plan.id}><summary>{plan.kind} · Version {plan.version} · {plan.status}</summary><p>{plan.rationale}</p><PlanChanges plan={plan} plans={data.plans} labels={data.catalogue_labels}/><Content value={plan.content} labels={data.catalogue_labels}/></details>)}{!data.plans.length&&<p className="muted">Your plan history will appear here.</p>}</>}</section>;
}

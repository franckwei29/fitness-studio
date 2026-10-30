import {useEffect,useState} from 'react';
import {Check,ContactRound} from 'lucide-react';
import {api} from './api';
type Assessment={id:string;version:number;status:string;proposed_facts:Record<string,unknown>;confirmed_facts:Record<string,unknown>};
type Profile={assessment:Assessment|null;snapshot:{facts:Record<string,unknown>}|null;member:{status:string}|null};
const required=['age','goal','experience','available_days','session_minutes','equipment','dietary_restrictions','prescribed_exercise','prescribed_nutrition','medical_supervision','pregnancy_postpartum','postoperative_acute_injury','resolved_past_injury'];
const display=(v:unknown)=>v===undefined?'Not provided':Array.isArray(v)?v.join(', ')||'None':typeof v==='boolean'?(v?'Yes':'No'):String(v);
export function ProfileReviewCard({onEdit}:{onEdit:()=>void}){
 const [data,setData]=useState<Profile|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[consent,setConsent]=useState(false);
 async function load(){try{setData(await api('/me/profile'));}catch(e){setError((e as Error).message);}}
 useEffect(()=>{void load();const t=setInterval(()=>void load(),5000);return()=>clearInterval(t);},[]);
 useEffect(()=>{setConsent(false);},[data?.assessment?.id,data?.assessment?.version,JSON.stringify(data?.assessment?.proposed_facts),JSON.stringify(data?.assessment?.confirmed_facts)]);
 const a=data?.assessment;const facts={...data?.snapshot?.facts,...a?.proposed_facts,...a?.confirmed_facts};const missing=required.filter(k=>facts[k]===undefined||facts[k]===null);const pending=a?.status==='PendingOwnerReview';const editable=a&&['Collecting','NeedsInformation'].includes(a.status);
 async function confirm(){if(!a||busy)return;setBusy(true);setError('');try{await api(`/assessments/${a.id}/confirm`,{expected_version:a.version,facts});setConsent(false);await load();window.dispatchEvent(new Event('workflow-changed'));}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const labels:Record<string,string>={age:'Age',goal:'Your goal',experience:'Training experience',available_days:'Days per week',session_minutes:'Minutes per session',equipment:'Available equipment',dietary_restrictions:'Dietary restrictions',prescribed_exercise:'Prescribed exercise programme',prescribed_nutrition:'Prescribed nutrition programme',medical_supervision:'Medical supervision',pregnancy_postpartum:'Pregnancy or postpartum',postoperative_acute_injury:'Surgery or acute injury recovery',resolved_past_injury:'Resolved past injury'};
 const hasPrevious=!!data?.snapshot;
 return <section className="member-action-card profile-review-card">
 <header><span className="type-icon" aria-hidden="true"><ContactRound size={24}/></span><div><span className="eyebrow">YOUR STARTING POINT</span><h3>Your profile review</h3></div></header>
 {!data?<p>Loading your details…</p>:<>
 {pending?<p className="member-receipt"><Check/>Submitted · Waiting for studio review</p>:!editable&&<p>{data.member?.status==='Accepted'?'Your profile is accepted.':a?.status||'Tell me about your goals to get started.'}</p>}
 <p className="profile-review-intro">Check your details before continuing. You can edit anything that does not look right.</p>
 <details open={!!editable}><summary>Review your details and changes</summary><div className="profile-fact-list">{required.map(k=><div className="profile-fact" key={k}><strong>{labels[k]}</strong><div>{hasPrevious&&<span className="profile-previous"><small>Previously saved</small>{display(data.snapshot?.facts[k])}</span>}<span className="profile-proposed">{hasPrevious&&<small>For review</small>}{display(facts[k])}</span></div></div>)}</div></details>
 <footer className="profile-review-footer">{editable&&(missing.length>0?<p>Please tell me: {missing.map(k=>labels[k]).join(', ')}.</p>:<label className="consent-row"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/><span>I have reviewed these details and confirm they are accurate.</span></label>)}
 <div className="action-group profile-review-actions"><button className="secondary" onClick={onEdit}>Edit details</button>{editable&&<button className="primary" disabled={busy||!consent||!!missing.length} onClick={()=>void confirm()}>{busy?'Submitting…':'Confirm my details'}</button>}</div></footer>
 </>}{error&&<p role="alert" className="error">{error}</p>}</section>;
}

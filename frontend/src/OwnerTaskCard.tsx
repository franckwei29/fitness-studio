import {useCallback,useEffect,useState} from 'react';
import {ArrowRight,Check,CheckCheck,ChevronRight,Clock3,FileCheck2,LoaderCircle,ShieldCheck,Sparkles,X} from 'lucide-react';
import {api} from './api';
import type {OwnerTarget} from './ownerNavigation';
export type OwnerTask={id:string;conversation_id:string;goal:string;status:string;fingerprint:string;actions:{name:string;target:string;arguments:string}[];changes:string[];remaining:string[];evidence:(OwnerTarget&{title:string})[];receipts:{action:string;target:OwnerTarget;status:string}[]};
const labels:Record<string,string>={'configuration.propose':'Prepare service settings','configuration.review':'Record configuration decision','configuration.execute':'Apply approved settings','approval.decide':'Record review decision','budget.change':'Update spending limit','allowance.change':'Update call allowance','members.assign':'Assign selected members','comparison.current':'Compare current model','release.confirm':'Confirm and publish release','member.assign':'Change member group','candidate.save':'Save agent candidate','comparison.prepare':'Prepare version comparison','release.propose':'Prepare release proposal','release.review':'Record release decision','release.execute':'Execute approved release','release.rollback':'Restore retained version','release.recovery':'Prepare rollback recovery','incident.prerequisites':'Set recovery prerequisites','incident.recover':'Request recovery','incident.verify':'Verify recovery','incident.close':'Close unresolved incident'};
const statuses:Record<string,string>={Ready:'Your decision',Executed:'Actions recorded',Dismissed:'Dismissed',Superseded:'Replaced by a newer proposal',Explained:'Investigation summary'};
export function confirmationDecision(text:string):'Execute'|'Dismiss'|null {
 const value=text.trim().replace(/[.!。！]+$/,'').toLowerCase();
 if(/^(confirm and (run|execute)|execute this (proposal|plan)|apply (this|that) exact (change|proposal)|approve and execute this version|确认并执行|批准并执行这一版|执行这个方案|就按这个方案执行)$/.test(value))return 'Execute';
 if(/^(dismiss this proposal|cancel this proposal|取消这个方案|放弃这个方案)$/.test(value))return 'Dismiss';
 return null;
}
export function OwnerTaskCard({id,onOpen,onChanged}:{id:string;onOpen:(target:OwnerTarget)=>void;onChanged:()=>Promise<void>}){
 const [task,setTask]=useState<OwnerTask|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const load=useCallback(async()=>{try{setTask(await api<OwnerTask>(`/owner/tasks/${id}`));}catch(e){setError((e as Error).message);}},[id]);
 useEffect(()=>{void load();const t=setInterval(()=>void load(),5000);return()=>clearInterval(t);},[load]);
 async function decide(decision:'Execute'|'Dismiss'){if(!task||busy)return;setBusy(true);setError('');try{setTask(await api<OwnerTask>(`/owner/tasks/${id}/decision`,{fingerprint:task.fingerprint,decision}));await onChanged();}catch(e){setError((e as Error).message);await load();}finally{setBusy(false);}}
 if(!task)return <div className="owner-agent-card"><p role="status">{error||'Opening the saved task…'}</p></div>;
 return <section className={`owner-agent-card owner-agent-${task.status.toLowerCase()}`} aria-label="Technical Manager task">
  <header className="owner-agent-heading"><span className="owner-agent-symbol">{task.status==='Executed'?<CheckCheck size={22}/>:<Sparkles size={22}/>}</span><div><span className="eyebrow">TECHNICAL MANAGER</span><h3>{task.goal}</h3></div><span className="owner-agent-status">{statuses[task.status]||task.status}</span></header>
  {!!task.changes.length&&<div className="owner-agent-changes"><h4>What this means</h4>{task.changes.map((text,i)=><p key={i}><Check size={15}/><span>{text}</span></p>)}</div>}
  {!!task.actions.length&&<ol className="owner-agent-steps">{task.actions.map((action,i)=><li key={i}><span className="owner-step-number">{task.status==='Executed'?<Check size={14}/>:i+1}</span><div><strong>{labels[action.name]||action.name}</strong><details><summary>Review exact parameters</summary><dl>{Object.entries(JSON.parse(action.arguments)).map(([key,value])=><div key={key}><dt>{key.replaceAll('_',' ')}</dt><dd>{key==='limit_micros'?`US$${Number(value)/1000000}`:typeof value==='object'?JSON.stringify(value,null,2):String(value)}</dd></div>)}</dl></details></div></li>)}</ol>}
  {!!task.evidence.length&&<div className="owner-agent-evidence"><h4>Records & evidence</h4>{task.evidence.map((link,i)=><button key={i} onClick={()=>onOpen(link)}><span><FileCheck2 size={17}/>{link.title}</span><ChevronRight size={17}/></button>)}</div>}
  {task.receipts.map((receipt,i)=><button className="owner-action-receipt" key={i} onClick={()=>onOpen(receipt.target)}><CheckCheck size={18}/><span><strong>{labels[receipt.action]||receipt.action}</strong><small>{receipt.status} · Open current record</small></span><ArrowRight size={16}/></button>)}
  {!!task.remaining.length&&<div className="owner-agent-next"><h4><Clock3 size={16}/>Still to do</h4>{task.remaining.map((text,i)=><p key={i}>{text}</p>)}</div>}
  {error&&<p className="error" role="alert">{error}</p>}
  {task.status==='Ready'&&<footer><p><ShieldCheck size={16}/>Review the scope above. Nothing in this proposal has been applied.</p><div><div className="action-group"><button className="primary" disabled={busy} onClick={()=>void decide('Execute')}>{busy?<LoaderCircle className="spin" size={17}/>:<CheckCheck size={17}/>}Confirm and run</button><button className="text-button" disabled={busy} onClick={()=>void decide('Dismiss')}><X size={16}/>Dismiss proposal</button></div></div><small>To adjust it, continue in the conversation. You can also say “Confirm and run”.</small></footer>}
 </section>;
}

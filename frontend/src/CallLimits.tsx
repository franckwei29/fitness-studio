import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowUpRight, CheckCircle2, Cpu, Search, ShieldCheck, Users, UserRound, Zap } from 'lucide-react';
import { api } from './api';
import { notifyOwnerChange, routeHash, readRoute, type OwnerTarget } from './ownerNavigation';

export type CallRule = {scope_key:string;label:string;calls:number|null;limit:number;version:number;fingerprint:string;source:string;applies_to:string;group_id?:string;group_name?:string;group_limit?:number;member_id?:string;email?:string;members?:number;inheriting_members?:number;used?:number;next_capacity_at?:string|null;provider?:string;model?:string;configured?:boolean};
export type LimitOverview = {models:CallRule[];groups:CallRule[];members:CallRule[];owner:CallRule;initial:CallRule;default_model:string;total:number;offset:number;has_more:boolean;usage_error?:string};
type Changed = (target?:OwnerTarget)=>void|Promise<void>;
export const limitTarget=(scope:string):OwnerTarget=>({page:'budgets',id:scope,section:'call-limit'});

export function LimitEditor({rule:initial,proposed,onChanged,confirm=false}:{rule:CallRule;proposed?:number|null;onChanged?:Changed;confirm?:boolean}){
 const [rule,setRule]=useState(initial),[value,setValue]=useState(String(proposed??initial.limit)),[inherit,setInherit]=useState(proposed===undefined?initial.calls===null:proposed===null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[done,setDone]=useState(false);
 const sending=useRef(false),dirty=useRef(false),isModel=rule.scope_key.startsWith('model:'),isMember=rule.scope_key.startsWith('member:');
 useEffect(()=>{if(confirm){let live=true;api<CallRule>(`/owner/call-limits/entry?scope=${encodeURIComponent(initial.scope_key)}`).then(current=>{if(live&&current.fingerprint!==initial.fingerprint){setDone(true);setNotice('This earlier preview is no longer current. Open settings to review the latest allowance.');}}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};}},[confirm,initial.scope_key,initial.fingerprint]);
 useEffect(()=>{if(!confirm&&!dirty.current){setRule(initial);setValue(String(initial.limit));setInherit(initial.calls===null);}},[initial.fingerprint,confirm]);
 async function refresh(){try{const current=await api<CallRule>(`/owner/call-limits/entry?scope=${encodeURIComponent(rule.scope_key)}`);dirty.current=false;setRule(current);setError('');setNotice('Current allowance loaded. Review before saving.');setDone(false);}catch(e){setError((e as Error).message);}}
 async function save(event:FormEvent){event.preventDefault();if(sending.current)return;sending.current=true;setBusy(true);setError('');setNotice('');
  try{const calls=inherit&&isMember?null:Number(value);if(calls!==null&&(!Number.isSafeInteger(calls)||calls<1||calls>1000000))throw new Error('Enter a whole number between 1 and 1,000,000.');
   const saved=await api<CallRule&{message:string}>('/owner/call-limits',{scope_key:rule.scope_key,calls,expected_fingerprint:rule.fingerprint,reason:confirm?'Owner confirmed conversation preview':'Owner saved AI usage settings'});
   dirty.current=false;setRule(saved);setDone(confirm);setNotice(`${saved.message} Existing usage is unchanged.`);notifyOwnerChange();await onChanged?.(limitTarget(rule.scope_key));
  }catch(e){setError((e as Error).message);}finally{sending.current=false;setBusy(false);}}
 if(done)return <p role="status" className="owner-receipt">{notice}</p>;
 return <form className="limit-editor" onSubmit={save}>
  <p className="limit-scope">{rule.applies_to}{isModel?'. All accounts share this model’s hourly allowance.':isMember?` · ${rule.group_name}`:rule.scope_key.startsWith('group:')?'. Each member has their own allowance.':'. Separate from member group allowances.'}</p>
  {isMember&&<div className="allowance-source"><span className="status-pill">{rule.source}</span><span>Group default: {rule.group_limit} calls / day</span></div>}
  {isMember&&<label>Allowance rule<select value={inherit?'inherit':'custom'} onChange={e=>{dirty.current=true;setInherit(e.target.value==='inherit');setDone(false);}}><option value="custom">Set custom allowance</option><option value="inherit">Use group default</option></select></label>}
  {!(isMember&&inherit)&&<label>{isModel?'Hourly limit':'Daily allowance per '+(isMember?'member':rule.scope_key.startsWith('group:')?'member':rule.scope_key==='initial'?'account':'owner')}<input type="number" required min={1} max={1000000} step={1} value={value} onChange={e=>{dirty.current=true;setValue(e.target.value);setDone(false);}}/></label>}
  {isModel&&<div className="limit-presets" aria-label="Hourly limit presets">{[100,200,500].map(n=><button key={n} type="button" className={value===String(n)?'preset selected':'preset'} onClick={()=>{dirty.current=true;setValue(String(n));setDone(false);}}>{n}</button>)}<span>or enter a custom limit</span></div>}
  <div className="owner-before-after"><span>Current<strong>{rule.limit} calls</strong></span><span>After saving<strong>{inherit&&isMember?rule.group_limit:value||'—'} calls / {isModel?'hour':'day'}</strong></span></div>
  {rule.inheriting_members!==undefined&&<p className="muted compact">Affects {rule.inheriting_members} members who inherit this rule. Personal exceptions stay unchanged. This is a daily allowance for each member, not a shared pool.</p>}
  {isMember&&<p className="muted compact">A custom allowance stays in place if this member changes groups. Restoring the default follows their current group.</p>}
  <p className="muted compact">Lowering a limit below current usage pauses further calls until capacity returns. Saved settings apply to subsequent calls. Calls already sent continue.</p>
  {error&&<div role="alert" className="error">{error}<button type="button" className="text-button" onClick={()=>void refresh()}>Refresh current allowance</button></div>}
  {notice&&<p role="status" className="owner-receipt"><CheckCircle2 size={18}/>{notice}</p>}
  {!done&&<button className="primary" disabled={busy}>{busy?'Saving…':confirm?'Confirm change':inherit&&isMember?'Restore group default':'Save changes'}</button>}
 </form>;
}

export function MemberAllowances({data,reload,onChanged,targetId}:{data:LimitOverview;reload:()=>void;onChanged?:Changed;targetId?:string}){
 const [query,setQuery]=useState(''),[applied,setApplied]=useState(''),[group,setGroup]=useState(targetId?.startsWith('group:')?targetId.slice(6):''),[custom,setCustom]=useState(false),[offset,setOffset]=useState(0),[results,setResults]=useState(data),[error,setError]=useState('');
 const [editing,setEditing]=useState<string|null>(targetId||null);
 const load=useCallback(async()=>{const params=new URLSearchParams({query:applied,custom_only:String(custom),offset:String(offset)});if(group)params.set('group_id',group);try{setResults(await api<LimitOverview>(`/owner/call-limits?${params}`));setError('');}catch(e){setError((e as Error).message);}},[applied,custom,group,offset]);
 useEffect(()=>{void load();const changed=()=>void load();window.addEventListener('owner-workspace-changed',changed);return()=>window.removeEventListener('owner-workspace-changed',changed);},[load,data]);
 const changed:Changed=async target=>{reload();await load();await onChanged?.(target);};
 const selectedGroup=data.groups.find(g=>g.scope_key===`group:${group}`);
 return <section className="limits-section"><h2><Users size={23}/> Member allowances</h2><p className="muted">Each member has a daily allowance shared across models. Personal exceptions override the group default. Studio model limits still apply.</p>
  <div className="allowance-group-grid">{data.groups.map(g=><article className="plan-card" key={g.scope_key}><h3>{g.label}</h3><strong>{g.limit} calls per member / day</strong><p className="muted">{g.members} members · {g.inheriting_members} use the group default</p><button className="secondary" onClick={()=>{setGroup(g.scope_key.slice(6));setEditing(g.scope_key);setOffset(0);}}>Edit group allowance</button></article>)}</div>
  {selectedGroup&&<article className="plan-card"><div className="section-heading"><h3>{selectedGroup.label}</h3><button className="text-button" onClick={()=>{setGroup('');setEditing(null);}}>All groups</button></div>{editing===selectedGroup.scope_key&&<LimitEditor key={selectedGroup.scope_key} rule={selectedGroup} onChanged={changed}/>}</article>}
  <form className="owner-search" onSubmit={e=>{e.preventDefault();setApplied(query);setOffset(0);}}><Search size={18}/><input aria-label="Search member allowances" placeholder="Search by name, email or group" value={query} onChange={e=>setQuery(e.target.value)}/><button className="secondary">Search</button></form>
  <label className="confirmation"><input type="checkbox" checked={custom} onChange={e=>{setCustom(e.target.checked);setOffset(0);}}/>Show members with custom allowances</label>
  {error&&<p role="alert" className="error">{error}</p>}
  <div className="allowance-members">{results.members.map(m=><article key={m.scope_key} className="plan-card"><div className="section-heading"><div><h3><UserRound size={18}/> {m.label}</h3><p className="muted">{m.email} · {m.group_name}</p></div><span className={`status-pill ${m.calls===null?'':'custom-allowance'}`}>{m.source}</span></div><p><strong>{m.limit} calls / day</strong>{m.used!==undefined&&` · ${m.used} used in the last 24 hours`}</p>{m.next_capacity_at&&<p className="muted">Next capacity: {new Date(m.next_capacity_at).toLocaleString()}</p>}<div className="owner-task-actions"><button className="secondary" onClick={()=>setEditing(editing===m.scope_key?null:m.scope_key)}>Edit allowance</button><a className="text-button" href={routeHash({page:'members',id:m.member_id},readRoute().conversation)}>View member <ArrowUpRight size={16}/></a></div>{editing===m.scope_key&&<LimitEditor key={m.scope_key} rule={m} onChanged={changed}/>}</article>)}</div>
  {!results.members.length&&<p className="muted">No matching members.</p>}<div className="owner-task-actions">{offset>0&&<button className="secondary" onClick={()=>setOffset(Math.max(0,offset-50))}>Previous members</button>}{results.has_more&&<button className="secondary" onClick={()=>setOffset(offset+50)}>More members</button>}</div>
  <details className="plan-card"><summary>Owner allowance · {data.owner.limit} calls / day</summary><p><ShieldCheck size={18}/> This allowance belongs to your owner account. It is separate from member groups.</p><p>{data.owner.used??'—'} calls used in the last 24 hours.</p><LimitEditor rule={data.owner} onChanged={changed}/></details>
  <details className="plan-card"><summary>Initial account allowance · {data.initial.limit} calls / day</summary><p>Applies before membership. Joining a group changes the rule without resetting recorded usage.</p><LimitEditor rule={data.initial} onChanged={changed}/></details>
 </section>;
}

export type QuotaCard={type:string;message:string;entries:{rule:CallRule;editing:boolean;proposed_calls?:number|null}[]};
export function OwnerQuotaCard({card,onOpen,onChanged}:{card:QuotaCard;onOpen:(target:OwnerTarget)=>void;onChanged?:Changed}){
 return <section className="owner-workspace-cards" aria-label="AI call allowances"><h3><Zap size={21}/> AI usage &amp; limits</h3><p>{card.message}</p>{card.entries.map(e=><article className="owner-task-card" key={e.rule.scope_key}><div className="owner-task-heading"><span className="owner-task-icon">{e.rule.model?<Cpu size={21}/>:<Users size={21}/>}</span><div><span className="owner-category">{e.rule.source}</span><h3>{e.rule.label}</h3></div>{!e.editing&&<span className="status-pill">{e.rule.limit} calls / {e.rule.model?'hour':'day'}</span>}</div>{e.editing?<LimitEditor rule={e.rule} proposed={e.proposed_calls} confirm onChanged={onChanged}/>:<p>{e.rule.applies_to}{e.rule.group_name&&` · ${e.rule.group_name}`} · {e.rule.source}</p>}<div className="owner-task-actions"><button className="secondary" onClick={()=>onOpen(limitTarget(e.rule.scope_key))}>Open settings <ArrowUpRight size={16}/></button>{e.rule.member_id&&<button className="text-button" onClick={()=>onOpen({page:'members',id:e.rule.member_id})}>View member <ArrowUpRight size={16}/></button>}</div></article>)}{!card.entries.length&&<button className="secondary" onClick={()=>onOpen({page:'budgets'})}>Open settings <ArrowUpRight size={16}/></button>}</section>;
}

export function AllowanceDetail({scope,onChanged}:{scope:string;onChanged?:Changed}){
 const [rule,setRule]=useState<CallRule|null>(null),[error,setError]=useState('');
 useEffect(()=>{let live=true;api<CallRule>(`/owner/call-limits/entry?scope=${encodeURIComponent(scope)}`).then(r=>{if(live)setRule(r);}).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[scope]);
 return <article className="plan-card"><h2>AI allowance</h2>{error&&<p role="alert" className="error">{error}</p>}{rule&&<><p><strong>{rule.label}</strong></p><LimitEditor key={rule.scope_key} rule={rule} onChanged={onChanged}/></>}</article>;
}

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowUpRight, CheckCheck, ChevronDown, ClipboardCheck, CircleGauge, ContactRound, Users, Wallet, TriangleAlert, FlaskConical, Rocket, Bell, LoaderCircle, RefreshCw, Search, Clock3, Settings2, Zap } from 'lucide-react';
import { api } from './api';
import { Groups } from './Groups';
import { Approvals } from './Approvals';
import { UsageAllowance } from './UsageAllowance';
import { notifyOwnerChange, type OwnerTarget } from './ownerNavigation';

export type OwnerWorkspaceCard = {type:string;area?:string;query?:string;target_id?:string;target?:OwnerTarget;proposal?:{amount_usd?:string;group_id?:string}};
type WorkspaceItem={id:string;category:string;title:string;status:string;description:string;target:OwnerTarget;action?:string;can_cancel?:boolean;metadata?:Record<string,unknown>};
type WorkspaceData={area:string;title:string;summary:string;metrics?:{total_members:number;active_members:number;pending_reviews:number;unknown_costs:number};items:WorkspaceItem[];total:number;offset:number;limit:number;has_more:boolean;checked_at:string};
type Budget={id:string;scope_key:string;limit_micros:number;spent_micros:number;reserved_micros:number;fingerprint:string};
type Member={id:string;display_name:string;group_id:string;status:string};
type Group={id:string;name:string};
type UnknownCall={id:string;role:string;reserved_micros:number;local_slot_held:boolean;can_release_local_slot:boolean};
type Props={card:OwnerWorkspaceCard;conversationId:string;revision:number;onOpen:(target:OwnerTarget)=>void;onChanged:(target?:OwnerTarget)=>Promise<void>};
const money=(value:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:6}).format(value/1000000);
const icons:Record<string,typeof Users>={approvals:ClipboardCheck,members:ContactRound,groups:Users,budgets:Wallet,incidents:TriangleAlert,versions:FlaskConical,releases:Rocket,dashboard:CircleGauge,notifications:Bell,requests:Clock3,usage:Zap};
export function BusinessIcon({page}:{page:string}){const Icon=icons[page]||Settings2;return <Icon size={19} aria-hidden="true"/>;}
function useFormDraft(key:string){
 const [value,setValue]=useState<Record<string,string>>(()=>{try{return JSON.parse(sessionStorage.getItem(key)||'{}');}catch{return {};}});
 function update(field:string,text:string){setValue(previous=>{const next={...previous,[field]:text};sessionStorage.setItem(key,JSON.stringify(next));return next;});}
 function clear(){sessionStorage.removeItem(key);setValue({});}
 return {value,update,clear};
}
function InlineEdit({item,conversationId,onChanged,proposal}:{item:WorkspaceItem;conversationId:string;onChanged:Props['onChanged'];proposal?:OwnerWorkspaceCard['proposal']}){
 const [budget,setBudget]=useState<Budget|null>(null),[member,setMember]=useState<Member|null>(null),[groups,setGroups]=useState<Group[]>([]),[call,setCall]=useState<UnknownCall|null>(null);
 const [loaded,setLoaded]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[receipt,setReceipt]=useState('');
 const sending=useRef(false),draft=useFormDraft(`owner-edit:${conversationId}:${item.action}:${item.id}`);
 const load=useCallback(async()=>{
  if(item.action==='budget'){const rows=await api<Budget[]>('/owner/budgets');const row=rows.find(r=>r.id===item.id||r.scope_key===item.target.id);if(!row)throw new Error('This budget is no longer available.');setBudget(row);}
  if(item.action==='member-group'){const [rows,g]=await Promise.all([api<Member[]>('/owner/members'),api<Group[]>('/owner/groups')]);const row=rows.find(r=>r.id===item.id)||await api<Member>(`/owner/members/${item.id}`);setMember(row);setGroups(g);}
  if(item.action==='release-slot'){const rows=await api<UnknownCall[]>('/owner/unknown-usage');const row=rows.find(r=>r.id===item.id);if(!row)throw new Error('This usage record is no longer available.');setCall(row);}
  setLoaded(true);
 },[item.id,item.action,item.target.id]);
 useEffect(()=>{void load().catch(e=>setError(e.message));},[load]);
 async function save(event:FormEvent){
  event.preventDefault();if(sending.current)return;sending.current=true;setBusy(true);setError('');setReceipt('');
  let target=item.target;
  try{
   const reason=(draft.value.reason||'').trim();if(!reason)throw new Error('Enter a reason for this change.');
   if(budget){const amount=Number(draft.value.amount??proposal?.amount_usd??budget.limit_micros/1000000);if(!Number.isFinite(amount)||amount<0||amount>1000)throw new Error('Enter a limit between US$0 and US$1,000.');const saved=await api<Budget>('/owner/budgets',{scope_key:budget.scope_key,limit_micros:Math.round(amount*1000000),expected_fingerprint:budget.fingerprint,reason});target={page:'budgets',id:saved.id};setReceipt(`Budget limit saved: ${money(saved.limit_micros)}.`);}
   if(member){const group=draft.value.group||proposal?.group_id||member.group_id;if(group===member.group_id)throw new Error('Choose a different service group.');await api(`/owner/members/${member.id}/group`,{group_id:group,expected_group_id:member.group_id,reason});setReceipt(`Group changed to ${groups.find(g=>g.id===group)?.name||'the selected group'}.`);}
   if(call){await api(`/owner/unknown-usage/${call.id}/release-slot`,{reason});setReceipt('Local slot released. Funds remain reserved.');}
   draft.clear();notifyOwnerChange();await onChanged(target);await load();
  }catch(e){setError(`${(e as Error).message} Check the latest record before trying again.`);notifyOwnerChange();try{await load();}catch{/* Preserve the first error and the pending form. */}}
  finally{sending.current=false;setBusy(false);}
 }
 const currentGroup=groups.find(g=>g.id===member?.group_id)?.name;
 return <div className="owner-inline-edit">
  {!loaded&&!error&&<p role="status"><LoaderCircle className="spin" size={16}/> Loading current details…</p>}
  {error&&<p role="alert" className="error">{error}</p>}
  {receipt&&<p role="status" className="owner-receipt"><CheckCheck size={18}/>{receipt}</p>}
  {loaded&&<form onSubmit={save}>
   {budget&&<><div className="owner-before-after"><span>Current limit<strong>{money(budget.limit_micros)}</strong></span><span>New limit<strong>{money(Math.round(Number(draft.value.amount??proposal?.amount_usd??budget.limit_micros/1000000)*1000000))}</strong></span></div><p className="muted compact">Spent {money(budget.spent_micros)} · Reserved {money(budget.reserved_micros)}. Call allowances stay unchanged.</p><label>Total limit (USD)<input type="number" min="0" max="1000" step="0.000001" required value={draft.value.amount??proposal?.amount_usd??String(budget.limit_micros/1000000)} onChange={e=>draft.update('amount',e.target.value)}/></label></>}
   {member&&<><p>Current group: <strong>{currentGroup}</strong></p><label>New service group<select value={draft.value.group||proposal?.group_id||member.group_id} onChange={e=>draft.update('group',e.target.value)}>{groups.map(g=><option key={g.id} value={g.id}>{g.name}</option>)}</select></label><p className="muted compact">Queued work and existing plan context will be checked again for the new group.</p></>}
   {call&&<p>Reserved: <strong>{money(call.reserved_micros)}</strong>. Releasing the stopped local slot does not refund charges or retry work.</p>}
   {(!call||call.local_slot_held)&&<><label>Reason<input required minLength={call?10:1} maxLength={1000} value={draft.value.reason||''} onChange={e=>draft.update('reason',e.target.value)}/></label><button type="submit" className="primary" disabled={busy||!!(call&&!call.can_release_local_slot)||!!(member&&(!(draft.value.group||proposal?.group_id)||(draft.value.group||proposal?.group_id)===member.group_id))}>{busy?<><LoaderCircle className="spin" size={16}/>Saving…</>:budget?'Confirm budget change':member?'Confirm group change':'Release slot and keep funds reserved'}</button>{call&&!call.can_release_local_slot&&<p className="muted compact">This call is not yet eligible for local slot release.</p>}</>}
  </form>}
 </div>;
}
function InlineAction({item,onChanged}:{item:WorkspaceItem;onChanged:Props['onChanged']}){
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[done,setDone]=useState(false);
 const submitting=useRef(false);
 const cancel=item.action==='request-cancel'||item.can_cancel;
 async function execute(){if(submitting.current)return;submitting.current=true;setBusy(true);setError('');try{
  if(cancel)await api(`/requests/${item.id}/cancel`,{});
  else if(item.action==='notification-read')await api(`/me/notifications/${item.id}/read`,{});
  else throw new Error('Open the full details to complete this action.');
  setDone(true);notifyOwnerChange();await onChanged(item.target);
 }catch(e){setError((e as Error).message);notifyOwnerChange();}finally{submitting.current=false;setBusy(false);}}
 return <div className="owner-inline-edit">{error&&<p role="alert" className="error">{error}</p>}{done?<p role="status" className="owner-receipt"><CheckCheck size={18}/>{cancel?'Activity cancelled.':'Update marked as read.'}</p>:<><p>{cancel?'Stop this update? Calls already sent may still count toward allowance. Existing plans remain unchanged.':'Mark this update as read in your workspace.'}</p><button className="primary" disabled={busy} onClick={()=>void execute()}>{busy?'Saving…':cancel?'Confirm cancellation':'Mark as read'}</button></>}</div>;
}
function OwnerItem({item,conversationId,onOpen,onChanged,proposal}:{item:WorkspaceItem;conversationId:string;onOpen:Props['onOpen'];onChanged:Props['onChanged'];proposal?:OwnerWorkspaceCard['proposal']}){
 const expandedKey=`owner-expanded:${conversationId}:${item.target.page}:${item.id}`;
 const [expanded,setExpanded]=useState(()=>sessionStorage.getItem(expandedKey)==='true');
 const label=item.action==='approval'?(item.category==='Membership'?'Review application':'Review proposal'):item.action==='budget'?'Adjust budget':item.action==='member-group'?'Change group':item.action==='release-slot'?'Review slot release':item.action==='apply-configuration'?'Review approved change':item.action==='notification-read'?'Read update':item.can_cancel?'Cancel update':null;
 const needsAttention=/review|attention|pending|blocked/i.test(item.status);
 return <article className={`owner-task-card owner-category-${item.target.page}`} data-record-id={item.id}>
  <div className="owner-task-heading"><span className="owner-task-icon"><BusinessIcon page={item.target.page}/></span><div><span className="owner-category">{item.category}</span><h3>{item.title}</h3></div><span className={`status-pill ${needsAttention?'attention':''}`}>{item.status}</span></div>
  <p>{item.description}</p>
  <div className="owner-task-actions">{label&&<button className="primary" aria-expanded={expanded} onClick={()=>{sessionStorage.setItem(expandedKey,String(!expanded));setExpanded(!expanded);}}>{label}<ChevronDown size={16} className={expanded?'rotated':''}/></button>}<button className="secondary" onClick={()=>onOpen(item.target)}>{item.target.section==='create'?(item.target.page==='versions'?'Create candidate':'Propose release'):item.target.section==='report'?'Open report':item.target.section==='trace'?'View evidence':item.target.page==='approvals'?'Open review details':item.target.page==='incidents'?'View recovery steps':item.target.page==='versions'?(item.target.section?.startsWith('evaluation:')?'Open comparison details':'Open version details'):item.target.page==='releases'?'Open release details':'Open details'}<ArrowUpRight size={16}/></button></div>
  {expanded&&<div className="owner-expanded">{item.action==='approval'?<Approvals targetId={item.id} embedded onChanged={()=>void onChanged(item.target)}/>:item.action==='apply-configuration'?<Groups embedded targetId={item.id} targetSection="change" onChanged={()=>void onChanged(item.target)}/>:item.action==='notification-read'||item.can_cancel?<InlineAction item={item} onChanged={onChanged}/>:['budget','member-group','release-slot'].includes(item.action||'')?<InlineEdit item={item} conversationId={conversationId} onChanged={onChanged} proposal={proposal}/>:<p role="status" className="owner-receipt"><CheckCheck size={18}/>This record has changed. Current status: {item.status}.</p>}</div>}
 </article>;
}
export function OwnerChatCards({card,conversationId,revision,onOpen,onChanged}:Props){
 const [data,setData]=useState<WorkspaceData|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[query,setQuery]=useState(card.query||''),[appliedQuery,setAppliedQuery]=useState(card.query||''),[limit,setLimit]=useState(5),[offset,setOffset]=useState(0);
 const generation=useRef(0);
 const load=useCallback(async()=>{const version=++generation.current;setLoading(true);try{const params=new URLSearchParams({area:card.area||'attention',query:appliedQuery,limit:String(limit),offset:String(offset)});if(card.target_id)params.set('target_id',card.target_id);const value=await api<WorkspaceData>(`/owner/workspace?${params}`);if(version===generation.current){setData(value);setError('');}}catch(e){if(version===generation.current)setError((e as Error).message);}finally{if(version===generation.current)setLoading(false);}},[card.area,card.target_id,appliedQuery,limit,offset]);
 useEffect(()=>{void load();const timer=setInterval(()=>void load(),15000);const changed=()=>void load();window.addEventListener('owner-workspace-changed',changed);return()=>{generation.current++;clearInterval(timer);window.removeEventListener('owner-workspace-changed',changed);};},[load,revision]);
 return <section className="owner-workspace-cards" aria-label={data?.title||'Studio workspace'}>
  <div className="owner-card-toolbar"><strong>{data?.title||'Studio workspace'}</strong><button className="text-button" onClick={()=>void load()} disabled={loading} aria-label="Refresh business cards"><RefreshCw size={15} className={loading?'spin':''}/>Refresh</button></div>
  {data?.summary&&<p className="owner-summary">{data.summary}</p>}
  {data?.metrics&&<div className="owner-metrics"><div><span>Members</span><strong>{data.metrics.total_members}</strong></div><div><span>Active members</span><strong>{data.metrics.active_members}</strong></div><div><span>Needs review</span><strong>{data.metrics.pending_reviews}</strong></div></div>}{card.area==='usage'&&<UsageAllowance/>}
  {error&&<p className="error" role="alert">{error}</p>}
  {!data&&loading&&<p role="status"><LoaderCircle className="spin" size={17}/>Loading current records…</p>}
  {data&&<>
   {['members','approvals','groups','versions','releases','incidents','budgets'].includes(card.area||'')&&<form className="owner-search" onSubmit={e=>{e.preventDefault();setAppliedQuery(query);setLimit(5);setOffset(0);}}><Search size={16}/><input aria-label={`Search ${card.area}`} placeholder="Find by name or reference" value={query} onChange={e=>setQuery(e.target.value)}/><button type="submit" className="text-button">Find</button></form>}
   <div className="owner-task-list">{data.items.map(item=><OwnerItem key={`${item.target.page}:${item.id}`} item={item} conversationId={conversationId} onOpen={onOpen} onChanged={onChanged} proposal={card.proposal}/>)}</div>
   {!data.items.length&&<div className="owner-empty"><CheckCheck size={21}/><p>No matching records. You can change the search or open the business page.</p></div>}
   {offset>0&&<button className="secondary owner-show-more" onClick={()=>setOffset(value=>Math.max(0,value-limit))}>Previous records</button>}{data.has_more&&<button className="secondary owner-show-more" onClick={()=>setOffset(value=>value+limit)}>Show more ({data.total} total)<ChevronDown size={16}/></button>}<button className="text-button" onClick={()=>onOpen({page:card.area==='attention'?'approvals':card.area==='usage'?'budgets':(card.area as OwnerTarget['page'])||'dashboard'})}>Open full {card.area==='attention'?'review queue':card.area==='usage'?'budget and usage details':data.title.toLowerCase()} <ArrowUpRight size={15}/></button>
   <small className="owner-checked">{loading?'Checking for updates…':`Checked ${new Date(data.checked_at.endsWith('Z')?data.checked_at:`${data.checked_at}Z`).toLocaleTimeString('en-AU',{hour:'2-digit',minute:'2-digit'})}`} · Saved records · No AI calls</small>
  </>}
 </section>;
}

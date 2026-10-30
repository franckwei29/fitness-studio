import {Modal} from './Modal';
import { ConversationMenu } from './ConversationMenu';
import { OwnerUpdates, type UpdatesData } from './OwnerUpdates';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, LogOut, MessageSquare, Plus, NotebookTabs, CircleGauge, Bell, ContactRound, Settings2, Users, ShieldCheck, Wallet, TriangleAlert, FlaskConical, Rocket, ArrowLeft } from 'lucide-react';
import { api, setCsrf } from './api';
import './style.css';
import { Intake } from './Intake';
import { Approvals } from './Approvals';
import { Plans } from './Plans';
import { Dashboard } from './Dashboard';
import { Notifications } from './Notifications';
import { Groups } from './Groups';
import { Incidents } from './Incidents';
import { Budgets } from './Budgets';
import { Members } from './Members';
import { AgentVersions } from './AgentVersions';
import { Releases } from './Releases';
import { Requests } from './Requests';
import { ConversationView, type Conversation, type Task, type History } from './ConversationView';
import { watchRequest } from './requestEvents';
import { readRoute, routeHash, ownerPages, notifyOwnerChange, type OwnerTarget, type Page } from './ownerNavigation';

type User = {id:string;display_name:string;role:string};
type Auth = {user:User;csrf_token:string};
function App() {
  const [user,setUser]=useState<User|null>(null),[loading,setLoading]=useState(true);
  const [error,setError]=useState(''),[register,setRegister]=useState(false),[busy,setBusy]=useState(false);
  const [route,setRoute]=useState(readRoute);
  const [sessions,setSessions]=useState<Conversation[]>([]),[selected,setSelected]=useState(readRoute().conversation || '');
  const [workspaceRevision,setWorkspaceRevision]=useState(0),[opening,setOpening]=useState(false);
  const navigationGeneration=useRef(0);
  const page=route.page;
  const [memberDirty,setMemberDirty]=useState(false),[discardMember,setDiscardMember]=useState(false);
  const [memberPanel,setMemberPanel]=useState<{page:'plans'|'intake'|'requests'|'notifications';section?:string}|null>(null);
  const [updates,setUpdates]=useState<UpdatesData|null>(null),[toast,setToast]=useState('');
  const seenUpdates=useRef<Set<string>|null>(null);
  useEffect(()=>{if(user?.role!=='Owner'){setUpdates(null);seenUpdates.current=null;return;}let active=true;
    const load=async()=>{try{const data=await api<UpdatesData>('/owner/updates');if(!active)return;const keys=new Set(data.items.filter(r=>r.state!=='Resolved'&&r.state!=='Monitoring').map(r=>r.id+':'+r.created_at));const fresh=[...keys].filter(k=>!seenUpdates.current?.has(k));if(fresh.length)setToast(`${fresh.length} ${seenUpdates.current?'new updates are':'updates are'} available.`);seenUpdates.current=keys;setUpdates(data);}catch{/* Preserve the last known count; Updates exposes errors. */}};
    void load();const timer=setInterval(()=>void load(),10000);window.addEventListener('owner-workspace-changed',load);window.addEventListener('focus',load);window.addEventListener('online',load);return()=>{active=false;clearInterval(timer);window.removeEventListener('owner-workspace-changed',load);window.removeEventListener('focus',load);window.removeEventListener('online',load);};
  },[user?.id,user?.role]);

  function navigate(target:OwnerTarget, conversation=selected){navigationGeneration.current++;window.location.hash=routeHash(target,conversation);setRoute({...target,conversation});}
  function setPage(value:Page){navigate({page:value});}
  useEffect(()=>{const changed=()=>{navigationGeneration.current++;const next=readRoute();setRoute(next);if(next.conversation)setSelected(next.conversation);};window.addEventListener('hashchange',changed);return()=>window.removeEventListener('hashchange',changed);},[]);
  const [history,setHistory]=useState<History|null>(null),[draft,setDraft]=useState('');
  const pendingSubmission=useRef<{content:string;id:string;key:string}|null>(null);
  const [operationsGroups,setOperationsGroups]=useState<{id:string;name:string}[]>([]),[operationsGroup,setOperationsGroup]=useState('');
  useEffect(()=>{if(user?.role==='Owner')api<{id:string;name:string}[]>('/owner/groups').then(setOperationsGroups).catch(e=>setError(e.message));else{setOperationsGroups([]);setOperationsGroup('');}},[user]);
  function signedIn(auth:Auth){setCsrf(auth.csrf_token);setUser(auth.user);}
  useEffect(()=>{api<Auth>('/auth/me').then(signedIn).catch(()=>{}).finally(()=>setLoading(false));},[]);
  useEffect(()=>{if(!user)return;let live=true;const navigation=navigationGeneration.current;api<Conversation[]>('/conversations').then(async rows=>{
    if(!live)return;setSessions(items=>navigation===navigationGeneration.current?rows:[...items,...rows.filter(row=>!items.some(item=>item.id===row.id))]);
    const stored=sessionStorage.getItem(`studio-conversation:${user.id}`);
    const requested=readRoute().conversation;
    let id=requested || (stored&&rows.some(row=>row.id===stored)?stored:'') || (user.role==='Owner'?rows[0]?.id:'');
    if(live&&id&&navigation===navigationGeneration.current)setSelected(id);
  }).catch(e=>{if(live)setError(e.message);});return()=>{live=false;};},[user]);
  useEffect(()=>{if(!user||!selected)return;sessionStorage.setItem(`studio-conversation:${user.id}`,selected);setDraft(sessionStorage.getItem(`studio-draft:${user.id}:${selected}`)||'');},[user?.id,selected]);
  function updateDraft(value:string){setDraft(value);if(user&&selected)sessionStorage.setItem(`studio-draft:${user.id}:${selected}`,value);}
  async function ownerChanged(target?:OwnerTarget){
    notifyOwnerChange();setWorkspaceRevision(value=>value+1);
    const actual=target || route;
    if(selected&&actual.id){try{await api(`/owner/conversations/${selected}/receipts`,{key:crypto.randomUUID(),target:{page:actual.page,id:actual.id,section:actual.section}});}catch(e){setError(`The latest record can still be viewed, but its conversation receipt could not be synchronized: ${(e as Error).message}`);}}
    await refreshHistory.current();
  }

  const refreshHistory=useRef<()=>Promise<void>>(async()=>{});
  const historyGeneration=useRef(0);
  useEffect(()=>{
    if(!selected){setHistory(null);return;}
    let disposed=false;
    const load=async()=>{
      const generation=++historyGeneration.current;
      try{const value=await api<History>(`/conversations/${selected}`);
        if(!disposed&&generation===historyGeneration.current)setHistory(value);
      }catch(e){if(!disposed&&generation===historyGeneration.current)setError((e as Error).message);}
    };
    refreshHistory.current=load;
    const refresh=()=>{if(document.visibilityState!=='hidden')void load();};
    void load();const timer=setInterval(refresh,5000);
    window.addEventListener('focus',refresh);window.addEventListener('online',refresh);window.addEventListener('workflow-changed',refresh);document.addEventListener('visibilitychange',refresh);
    return ()=>{disposed=true;historyGeneration.current++;clearInterval(timer);window.removeEventListener('focus',refresh);window.removeEventListener('online',refresh);window.removeEventListener('workflow-changed',refresh);document.removeEventListener('visibilitychange',refresh);refreshHistory.current=async()=>{};};
  },[selected]);
  const pendingRequests=history?.conversation.id===selected?history.requests.filter(task=>!['Completed','Stopped','Cancelled'].includes(task.status)).map(task=>task.id).sort().join(','):'';
  useEffect(()=>{
    let timer:ReturnType<typeof setTimeout>|undefined;
    const refresh=()=>{if(timer)clearTimeout(timer);timer=setTimeout(()=>void refreshHistory.current(),25);};
    const close=pendingRequests?pendingRequests.split(',').map(id=>watchRequest(id,refresh)):[];
    return ()=>{if(timer)clearTimeout(timer);close.forEach(stop=>stop());};
  },[selected,pendingRequests]);
  async function analyzeIncident(incidentId:string){
    const id=selected||await newConversation();
    await api(`/conversations/${id}/messages`,{key:crypto.randomUUID(),purpose:'Operations',incident_id:incidentId,content:'Analyze this incident. Separate observed facts from possible causes and suggest the next checks without executing changes.'});
    setSessions(await api<Conversation[]>('/conversations'));
    setSelected(id);navigate({page:'chat'},id);await refreshHistory.current();
  }
  async function authenticate(event:FormEvent<HTMLFormElement>){
    event.preventDefault();setBusy(true);setError('');
    const fields=Object.fromEntries(new FormData(event.currentTarget));
    try{signedIn(await api<Auth>(register?'/auth/register':'/auth/login',fields));}catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function newConversation(){
    navigationGeneration.current++;setOpening(true);
    try{const row=await api<Conversation>('/conversations',{});setSessions(items=>[row,...items.filter(item=>item.id!==row.id)]);setSelected(row.id);navigate({page:'chat'},row.id);return row.id;}finally{setOpening(false);}
  }
  async function submit(content:string,action?:string,source?:string){
    if(!content.trim()||busy)return;setBusy(true);setError('');
    try{const id=selected||await newConversation();
      const signature=JSON.stringify([content,action,source]);
      const pending=pendingSubmission.current;
      const key=pending?.content===signature&&pending.id===id?pending.key:crypto.randomUUID();
      pendingSubmission.current={content:signature,id,key};
      await api(`/conversations/${id}/messages`,{content,key,purpose:user?.role==='Owner'?'Operations':'Auto',action,source_request_id:source,operations_group_id:user?.role==='Owner'?(operationsGroup||undefined):undefined});
      pendingSubmission.current=null;updateDraft('');
      const value=await api<History>(`/conversations/${id}`);setHistory(value);
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function send(event:FormEvent){event.preventDefault();await submit(draft);}
  function openTarget(target:'chat'|'plans',conversation?:string,section?:string){if(conversation)setSelected(conversation);navigate({page:target,section},conversation||selected);}
  function discuss(text:string){setMemberPanel(null);updateDraft(text);setPage('chat');}
  async function startMemberAction(kind:string,planKind?:string){
    if(busy)return;setBusy(true);setError('');
    try{const id=selected||await newConversation();await api(`/conversations/${id}/member-actions`,{kind,details:planKind?{plan_kind:planKind}:undefined});setMemberPanel(null);navigate({page:'chat'},id);setHistory(await api<History>(`/conversations/${id}`));}catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function cancelRequest(id:string){
    if(busy)return;setBusy(true);setError('');
    try{await api<Task>(`/requests/${id}/cancel`,{});
      if(user?.role==='Owner')await ownerChanged({page:'requests',id});else await refreshHistory.current();
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function signOut(){try{await api('/auth/logout',{});setUser(null);setSelected('');setSessions([]);setDraft('');setHistory(null);setCsrf('');window.location.hash='';setRoute({page:'chat'});}catch(e){setError((e as Error).message);}}
  if(loading)return <main className="loading">Opening your studio…</main>;
  if(!user)return <main className="auth-layout"><section className="intro"><div className="brand"><Activity/> FITNESS STUDIO</div><span className="eyebrow">BUILD A ROUTINE THAT FITS</span><h1>Your goals.<br/>A clearer path.</h1><p>Personal training and nutrition guidance, connected in one thoughtful workspace.</p><div className="intro-foot">Move with purpose. Build with consistency.</div></section><section className="auth-panel"><form onSubmit={authenticate}><span className="eyebrow">YOUR PERSONAL WORKSPACE</span><h2>{register?'Start your journey':'Welcome back'}</h2><p className="muted">{register?'Create an account to begin your intake.':'Sign in to continue your fitness journey.'}</p>{register&&<label>Full name<input name="display_name" required maxLength={100} autoComplete="name"/></label>}<label>Email address<input name="email" type="email" required autoComplete="email"/></label><label>Password<input name="password" type="password" required minLength={10} maxLength={128} autoComplete={register?'new-password':'current-password'}/></label>{error&&<p role="alert" className="error">{error}</p>}<button className="primary" disabled={busy}>{busy?'Please wait…':register?'Create account':'Sign in'}</button><button type="button" className="text-button" onClick={()=>{setRegister(!register);setError('');}}>{register?'Already have an account? Sign in':'New here? Create an account'}</button></form></section></main>;
  const destinations=user.role==='Owner'?[['dashboard','Studio overview'],['approvals','Review queue'],['members','Members'],['groups','Member groups'],['incidents','Incidents'],['budgets','AI usage & limits'],['versions','Agent versions'],['releases','Agent releases']]:[['plans','My plans'],['intake','My profile']];
  const navIcons:Partial<Record<Page,React.ReactNode>>={dashboard:<CircleGauge size={20}/>,approvals:<ShieldCheck size={20}/>,members:<ContactRound size={20}/>,groups:<Users size={20}/>,incidents:<TriangleAlert size={20}/>,budgets:<Wallet size={20}/>,versions:<FlaskConical size={20}/>,releases:<Rocket size={20}/>};
  const detailProps={targetId:route.id,targetSection:route.section,onChanged:(target?:OwnerTarget)=>void ownerChanged(target)};
  const allowed=user.role==='Owner'||!ownerPages.includes(page);
  return <div className="workspace sage-studio"><aside><div className="brand"><span className="brand-symbol"><Activity/></span>Fitness Studio</div><button className="new-chat" disabled={busy||opening} onClick={()=>newConversation().catch(e=>setError(e.message))}><Plus size={18}/>New conversation</button><div className="main-navigation"><button className={`profile-nav ${page==='chat'?'selected':''}`} onClick={()=>setPage('chat')}><MessageSquare size={20}/>Assistant</button>{destinations.map(([id,label])=><button key={id} className={`profile-nav ${page===id?'selected':''}`} onClick={()=>setPage(id as typeof page)}>{id==='plans'?<NotebookTabs size={20}/>:id==='intake'?<ContactRound size={20}/>:(navIcons[id as Page]||<Settings2 size={20}/>)} {label}</button>)}<button className={`profile-nav ${page==='requests'?'selected':''}`} onClick={()=>setPage('requests')}><CircleGauge size={20}/>Activity</button><button className={`profile-nav ${page==='notifications'?'selected':''}`} onClick={()=>setPage('notifications')}><Bell size={20}/>Updates{user.role==='Owner'&&!!updates?.needs_action&&<span className="updates-count">{updates.needs_action}</span>}</button></div><label className="mobile-history">Conversation<select aria-label="Open a conversation" value={selected} onChange={e=>{setSelected(e.target.value);navigate({page:'chat'},e.target.value);}}><option value="">Choose a conversation</option>{sessions.map(session=><option key={session.id} value={session.id}>{session.title}</option>)}</select></label><span className="eyebrow">CONVERSATIONS</span><nav>{sessions.map(session=><div className="conversation-row" key={session.id}><button className={selected===session.id?'active':''} onClick={()=>{setSelected(session.id);navigate({page:'chat'},session.id);}}><MessageSquare size={16}/><span>{session.title}</span></button><ConversationMenu conversation={session} onRenamed={title=>{setSessions(rows=>rows.map(r=>r.id===session.id?{...r,title}:r));setHistory(h=>h?.conversation.id===session.id?{...h,conversation:{...h.conversation,title}}:h);}} onDeleted={()=>{setSessions(rows=>rows.filter(r=>r.id!==session.id));if(user){sessionStorage.removeItem(`studio-draft:${user.id}:${session.id}`);if(sessionStorage.getItem(`studio-conversation:${user.id}`)===session.id)sessionStorage.removeItem(`studio-conversation:${user.id}`);}if(selected===session.id){historyGeneration.current++;setSelected('');setHistory(null);setDraft('');pendingSubmission.current=null;navigate({page:'chat'},'');}}} onActivity={()=>navigate({page:'requests'},session.id)}/></div>)}</nav><div className="account"><span className="avatar">{user.display_name.slice(0,1)}</span><div><strong>{user.display_name}</strong><small>{user.role==='Owner'?'Studio owner':'Member workspace'}</small></div><button aria-label="Sign out" onClick={signOut}><LogOut size={18}/></button></div></aside><main className="conversation">{user.role==='Owner'&&!!updates?.needs_action&&<div className="attention-banner"><Bell size={20}/><strong>{updates.needs_action} items need your attention</strong><button className="secondary" onClick={()=>setPage('notifications')}>Review updates</button></div>}{user.role==='Owner'&&toast&&<div className="updates-toast" role="status"><Bell/><span>{toast}</span><button onClick={()=>{setPage('notifications');setToast('');}}>Review now</button><button aria-label="Dismiss notification" onClick={()=>setToast('')}>×</button></div>}{error&&<p role="alert" className="error banner">{error}</p>}{page!=='chat'&&<div className="workspace-return"><button className="text-button" onClick={()=>setPage('chat')}><ArrowLeft size={17}/>Back to conversation</button><span>Your conversation stays open</span></div>}
    {page!=='chat'&&<div className="business-surface" key={`${page}:${route.id||''}:${route.section||''}`}>{!allowed?<section className="intake-page"><h1>Owner access required</h1><p>This business record is available only to a studio owner.</p></section>:page==='requests'?<Requests targetId={route.id} onChanged={user.role==='Owner'?(target)=>void ownerChanged(target):undefined} onOpen={openTarget}/>:page==='intake'?<Intake/>:page==='approvals'?<Approvals {...detailProps}/>:page==='plans'?<Plans onMemberAction={(kind,plan)=>void startMemberAction(kind,plan)} onDiscuss={discuss} section={route.section} onSection={section=>navigate({page:'plans',section})}/>:page==='dashboard'?<Dashboard {...detailProps}/>:page==='notifications'&&user.role==='Owner'?<OwnerUpdates conversationId={selected} onOpen={navigate} onChanged={ownerChanged} targetId={route.id}/>:page==='notifications'?<Notifications onFeedback={()=>void startMemberAction("Feedback","Training")} targetId={route.id} onChanged={user.role==='Owner'?detailProps.onChanged:undefined} onNavigate={(page,section,conversation)=>navigate({page,section},conversation||selected)}/>:page==='groups'?<Groups {...detailProps}/>:page==='incidents'?<Incidents {...detailProps} onAnalyze={analyzeIncident}/>:page==='budgets'?<Budgets {...detailProps}/>:page==='members'?<Members {...detailProps}/>:page==='versions'?<AgentVersions {...detailProps}/>:page==='releases'?<Releases {...detailProps}/>:null}</div>}
    <div className="persistent-conversation" hidden={page!=='chat'}>
      {user.role==='Owner'&&operationsGroups.length>1&&<label className="operations-picker">Agent configuration<select value={operationsGroup} onChange={e=>setOperationsGroup(e.target.value)}><option value="">Default</option>{operationsGroups.filter(g=>g.name!=='Default').map(g=><option key={g.id} value={g.id}>{g.name}</option>)}</select></label>}
      <ConversationView key={selected||'welcome'} history={history?.conversation.id===selected?history:null} draft={draft} setDraft={updateDraft} busy={busy||opening} owner={user.role==='Owner'} onSend={send} onAction={(action,content,source)=>void submit(content,action,source)} onCancel={cancelRequest} onPage={(page,section)=>user.role==='Member'?setMemberPanel({page,section}):navigate({page,section})} onOwnerOpen={navigate} onOwnerChanged={ownerChanged} workspaceRevision={workspaceRevision} onOwnerPrompt={content=>void submit(content)} onMemberStart={(kind,plan)=>void startMemberAction(kind,plan)}/>
    </div>{user.role==='Member'&&memberPanel&&<Modal title={memberPanel.page==='plans'?'Your plans':memberPanel.page==='intake'?'Your details':memberPanel.page==='requests'?'Your activity':'Your updates'} onClose={()=>{if(memberDirty)setDiscardMember(true);else setMemberPanel(null);}}><div className="member-workspace-modal">{discardMember&&<div className="assessment-status"><p>You have unsaved profile edits.</p><button onClick={()=>setDiscardMember(false)}>Keep editing</button><button onClick={()=>{setMemberDirty(false);setDiscardMember(false);setMemberPanel(null);}}>Discard edits</button></div>}<p className="modal-context">Your conversation stays here. Close this window to continue chatting.</p>{memberPanel.page==='plans'?<Plans onMemberAction={(kind,plan)=>void startMemberAction(kind,plan)} onDiscuss={discuss} section={memberPanel.section} onSection={section=>setMemberPanel({page:'plans',section})}/>:memberPanel.page==='intake'?<Intake onDirtyChange={setMemberDirty}/>:memberPanel.page==='requests'?<Requests onOpen={(target,conversation,section)=>{if(target==='plans')setMemberPanel({page:'plans',section});else{setMemberPanel(null);openTarget(target,conversation,section);}}}/>:<Notifications onFeedback={()=>void startMemberAction("Feedback","Training")} onNavigate={(target,section)=>{if(['plans','intake','requests','notifications'].includes(target))setMemberPanel({page:target as 'plans'|'intake'|'requests'|'notifications',section});}}/>}</div></Modal>}</main></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);

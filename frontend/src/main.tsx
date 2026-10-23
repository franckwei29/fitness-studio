import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, LogOut, MessageSquare, Plus, NotebookTabs, CircleGauge, Bell, ContactRound, Settings2 } from 'lucide-react';
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

type User = {id:string;display_name:string;role:string};
type Auth = {user:User;csrf_token:string};
function App() {
  const [user,setUser]=useState<User|null>(null),[loading,setLoading]=useState(true);
  const [error,setError]=useState(''),[register,setRegister]=useState(false),[busy,setBusy]=useState(false);
  const [sessions,setSessions]=useState<Conversation[]>([]),[selected,setSelected]=useState('');
  const [history,setHistory]=useState<History|null>(null),[draft,setDraft]=useState('');
  const pendingSubmission=useRef<{content:string;id:string;key:string}|null>(null);
  const [operationsGroups,setOperationsGroups]=useState<{id:string;name:string}[]>([]),[operationsGroup,setOperationsGroup]=useState('');
  useEffect(()=>{if(user?.role==='Owner')api<{id:string;name:string}[]>('/owner/groups').then(setOperationsGroups).catch(e=>setError(e.message));else{setOperationsGroups([]);setOperationsGroup('');}},[user]);
  const [page,setPage]=useState<'chat'|'intake'|'approvals'|'plans'|'dashboard'|'notifications'|'groups'|'incidents'|'budgets'|'members'|'versions'|'releases'|'requests'>('chat');
  function signedIn(auth:Auth){setCsrf(auth.csrf_token);setUser(auth.user);}
  useEffect(()=>{api<Auth>('/auth/me').then(signedIn).catch(()=>{}).finally(()=>setLoading(false));},[]);
  useEffect(()=>{if(user) api<Conversation[]>('/conversations').then(setSessions).catch(e=>setError(e.message));},[user]);
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
    void load();const timer=setInterval(()=>void load(),15000);
    return ()=>{disposed=true;historyGeneration.current++;clearInterval(timer);refreshHistory.current=async()=>{};};
  },[selected]);
  const pendingRequests=history?.conversation.id===selected?history.requests.filter(task=>!['Completed','Stopped','Cancelled'].includes(task.status)).map(task=>task.id).sort().join(','):'';
  useEffect(()=>{
    let timer:ReturnType<typeof setTimeout>|undefined;
    const refresh=()=>{if(timer)clearTimeout(timer);timer=setTimeout(()=>void refreshHistory.current(),25);};
    const close=pendingRequests?pendingRequests.split(',').map(id=>watchRequest(id,refresh)):[];
    return ()=>{if(timer)clearTimeout(timer);close.forEach(stop=>stop());};
  },[selected,pendingRequests]);
  async function analyzeIncident(incidentId:string){
    const conversation=await api<Conversation>('/conversations',{});
    await api(`/conversations/${conversation.id}/messages`,{key:crypto.randomUUID(),purpose:'Operations',incident_id:incidentId,content:'Analyze this incident. Separate observed facts from possible causes and suggest the next checks without executing changes.'});
    setSessions(await api<Conversation[]>('/conversations'));
    setSelected(conversation.id);setPage('chat');
  }
  async function authenticate(event:FormEvent<HTMLFormElement>){
    event.preventDefault();setBusy(true);setError('');
    const fields=Object.fromEntries(new FormData(event.currentTarget));
    try{signedIn(await api<Auth>(register?'/auth/register':'/auth/login',fields));}catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function newConversation(){
    const row=await api<Conversation>('/conversations',{});
    setSessions(items=>[row,...items]);setPage('chat');setSelected(row.id);return row.id;
  }
  async function submit(content:string,action?:string,source?:string){
    if(!content.trim()||busy)return;setBusy(true);setError('');
    try{const id=selected||await newConversation();
      const signature=JSON.stringify([content,action,source]);
      const pending=pendingSubmission.current;
      const key=pending?.content===signature&&pending.id===id?pending.key:crypto.randomUUID();
      pendingSubmission.current={content:signature,id,key};
      await api(`/conversations/${id}/messages`,{content,key,purpose:user?.role==='Owner'?'Operations':'Auto',action,source_request_id:source,operations_group_id:user?.role==='Owner'?(operationsGroup||undefined):undefined});
      pendingSubmission.current=null;setDraft('');
      const value=await api<History>(`/conversations/${id}`);setHistory(value);
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function send(event:FormEvent){event.preventDefault();await submit(draft);}
  function openTarget(target:'chat'|'plans',conversation?:string){if(conversation)setSelected(conversation);setPage(target);}
  function discuss(text:string){setDraft(text);setPage('chat');}
  async function cancelRequest(id:string){
    if(busy)return;setBusy(true);setError('');
    try{await api<Task>(`/requests/${id}/cancel`,{});
      await refreshHistory.current();
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function signOut(){try{await api('/auth/logout',{});setUser(null);setSelected('');setSessions([]);setCsrf('');}catch(e){setError((e as Error).message);}}
  if(loading)return <main className="loading">Opening your studio…</main>;
  if(!user)return <main className="auth-layout"><section className="intro"><div className="brand"><Activity/> FITNESS STUDIO</div><span className="eyebrow">BUILD A ROUTINE THAT FITS</span><h1>Your goals.<br/>A clearer path.</h1><p>Personal training and nutrition guidance, connected in one thoughtful workspace.</p><div className="intro-foot">Move with purpose. Build with consistency.</div></section><section className="auth-panel"><form onSubmit={authenticate}><span className="eyebrow">YOUR PERSONAL WORKSPACE</span><h2>{register?'Start your journey':'Welcome back'}</h2><p className="muted">{register?'Create an account to begin your intake.':'Sign in to continue your fitness journey.'}</p>{register&&<label>Full name<input name="display_name" required maxLength={100} autoComplete="name"/></label>}<label>Email address<input name="email" type="email" required autoComplete="email"/></label><label>Password<input name="password" type="password" required minLength={10} maxLength={128} autoComplete={register?'new-password':'current-password'}/></label>{error&&<p role="alert" className="error">{error}</p>}<button className="primary" disabled={busy}>{busy?'Please wait…':register?'Create account':'Sign in'}</button><button type="button" className="text-button" onClick={()=>{setRegister(!register);setError('');}}>{register?'Already have an account? Sign in':'New here? Create an account'}</button></form></section></main>;
  const destinations=user.role==='Owner'?[['dashboard','Studio overview'],['approvals','Review queue'],['members','Members'],['groups','Member groups'],['incidents','Incidents'],['budgets','Budget limits'],['versions','Agent versions'],['releases','Agent releases']]:[['plans','My plans'],['intake','My profile']];
  return <div className="workspace sage-studio"><aside><div className="brand"><span className="brand-symbol"><Activity/></span>Fitness Studio</div><button className="new-chat" onClick={()=>newConversation().catch(e=>setError(e.message))}><Plus size={18}/>New conversation</button><div className="main-navigation"><button className={`profile-nav ${page==='chat'?'selected':''}`} onClick={()=>setPage('chat')}><MessageSquare size={20}/>Assistant</button>{destinations.map(([id,label])=><button key={id} className={`profile-nav ${page===id?'selected':''}`} onClick={()=>setPage(id as typeof page)}>{id==='plans'?<NotebookTabs size={20}/>:id==='intake'?<ContactRound size={20}/>:<Settings2 size={20}/>} {label}</button>)}<button className={`profile-nav ${page==='requests'?'selected':''}`} onClick={()=>setPage('requests')}><CircleGauge size={20}/>Activity</button><button className={`profile-nav ${page==='notifications'?'selected':''}`} onClick={()=>setPage('notifications')}><Bell size={20}/>Updates</button></div><label className="mobile-history">Conversation<select aria-label="Open a conversation" value={selected} onChange={e=>{setSelected(e.target.value);setPage('chat');}}><option value="">Choose a conversation</option>{sessions.map(session=><option key={session.id} value={session.id}>{session.title}</option>)}</select></label><span className="eyebrow">CONVERSATIONS</span><nav>{sessions.map(session=><button key={session.id} className={selected===session.id?'active':''} onClick={()=>{setPage('chat');setSelected(session.id);}}><MessageSquare size={16}/>{session.title}</button>)}</nav><div className="account"><span className="avatar">{user.display_name.slice(0,1)}</span><div><strong>{user.display_name}</strong><small>{user.role==='Owner'?'Studio owner':'Member workspace'}</small></div><button aria-label="Sign out" onClick={signOut}><LogOut size={18}/></button></div></aside><main className="conversation">{error&&<p role="alert" className="error banner">{error}</p>}{page==='requests'?<Requests onOpen={openTarget}/>:page==='intake'?<Intake/>:page==='approvals'?<Approvals/>:page==='plans'?<Plans onDiscuss={discuss}/>:page==='dashboard'?<Dashboard/>:page==='notifications'?<Notifications onNavigate={setPage}/>:page==='groups'?<Groups/>:page==='incidents'?<Incidents onAnalyze={analyzeIncident}/>:page==='budgets'?<Budgets/>:page==='members'?<Members/>:page==='versions'?<AgentVersions/>:page==='releases'?<Releases/>:<>{user.role==='Owner'&&operationsGroups.length>1&&<label className="operations-picker">Agent configuration<select value={operationsGroup} onChange={e=>setOperationsGroup(e.target.value)}><option value="">Default</option>{operationsGroups.filter(g=>g.name!=='Default').map(g=><option key={g.id} value={g.id}>{g.name}</option>)}</select></label>}<ConversationView history={history?.conversation.id===selected?history:null} draft={draft} setDraft={setDraft} busy={busy} owner={user.role==='Owner'} onSend={send} onAction={(action,content,source)=>void submit(content,action,source)} onCancel={cancelRequest} onPage={setPage}/></>}</main></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);

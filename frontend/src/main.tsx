import { useEffect, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { Activity, ArrowUp, LogOut, MessageSquare, Plus, ShieldCheck } from 'lucide-react';
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

type User = {id:string;display_name:string;role:string};
type Auth = {user:User;csrf_token:string};
type Conversation = {id:string;title:string;active_role:string};
type Message = {id:string;sender:string;content:string;cards?:{type:string}[]};
type Task = {id:string;status:string;kind:string;status_message?:string};
type History = {conversation:Conversation;messages:Message[];requests:Task[]};

function App() {
  const [user,setUser]=useState<User|null>(null),[loading,setLoading]=useState(true);
  const [error,setError]=useState(''),[register,setRegister]=useState(false),[busy,setBusy]=useState(false);
  const [sessions,setSessions]=useState<Conversation[]>([]),[selected,setSelected]=useState('');
  const [history,setHistory]=useState<History|null>(null),[draft,setDraft]=useState('');
  const [purpose,setPurpose]=useState('Chat');
  const [operationsGroups,setOperationsGroups]=useState<{id:string;name:string}[]>([]),[operationsGroup,setOperationsGroup]=useState('');
  useEffect(()=>{if(user?.role==='Owner')api<{id:string;name:string}[]>('/owner/groups').then(setOperationsGroups).catch(e=>setError(e.message));else{setOperationsGroups([]);setOperationsGroup('');}},[user]);
  const [page,setPage]=useState<'chat'|'intake'|'approvals'|'plans'|'dashboard'|'notifications'|'groups'|'incidents'|'budgets'|'members'|'versions'|'releases'>('chat');
  function signedIn(auth:Auth){setCsrf(auth.csrf_token);setUser(auth.user);}
  useEffect(()=>{api<Auth>('/auth/me').then(signedIn).catch(()=>{}).finally(()=>setLoading(false));},[]);
  useEffect(()=>{if(user) api<Conversation[]>('/conversations').then(setSessions).catch(e=>setError(e.message));},[user]);
  useEffect(()=>{
    if(!selected){setHistory(null);return;}
    let disposed=false;
    const load=()=>api<History>(`/conversations/${selected}`).then(value=>{if(!disposed)setHistory(value);}).catch(e=>{if(!disposed)setError(e.message);});
    load();const timer=setInterval(load,2500);
    return ()=>{disposed=true;clearInterval(timer);};
  },[selected]);
  async function authenticate(event:FormEvent<HTMLFormElement>){
    event.preventDefault();setBusy(true);setError('');
    const fields=Object.fromEntries(new FormData(event.currentTarget));
    try{signedIn(await api<Auth>(register?'/auth/register':'/auth/login',fields));}catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function newConversation(){
    const row=await api<Conversation>('/conversations',{});
    setSessions(items=>[row,...items]);setPage('chat');setSelected(row.id);return row.id;
  }
  async function send(event:FormEvent){
    event.preventDefault();if(!draft.trim()||busy)return;setBusy(true);setError('');
    try{const id=selected||await newConversation();
      await api(`/conversations/${id}/messages`,{content:draft,key:crypto.randomUUID(),purpose,operations_group_id:user?.role==='Owner'?(operationsGroup||undefined):undefined});
      setDraft('');setHistory(await api<History>(`/conversations/${id}`));
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function signOut(){try{await api('/auth/logout',{});setUser(null);setSelected('');setSessions([]);setCsrf('');}catch(e){setError((e as Error).message);}}
  if(loading)return <main className="loading">Opening your studio…</main>;
  if(!user)return <main className="auth-layout"><section className="intro"><div className="brand"><Activity/> FITNESS STUDIO</div><span className="eyebrow">BUILD A ROUTINE THAT FITS</span><h1>Your goals.<br/>A clearer path.</h1><p>Personal training and nutrition guidance, connected in one thoughtful workspace.</p><div className="intro-foot">Move with purpose. Build with consistency.</div></section><section className="auth-panel"><form onSubmit={authenticate}><span className="eyebrow">YOUR PERSONAL WORKSPACE</span><h2>{register?'Start your journey':'Welcome back'}</h2><p className="muted">{register?'Create an account to begin your intake.':'Sign in to continue your fitness journey.'}</p>{register&&<label>Full name<input name="display_name" required maxLength={100} autoComplete="name"/></label>}<label>Email address<input name="email" type="email" required autoComplete="email"/></label><label>Password<input name="password" type="password" required minLength={10} maxLength={128} autoComplete={register?'new-password':'current-password'}/></label>{error&&<p role="alert" className="error">{error}</p>}<button className="primary" disabled={busy}>{busy?'Please wait…':register?'Create account':'Sign in'}</button><button type="button" className="text-button" onClick={()=>{setRegister(!register);setError('');}}>{register?'Already have an account? Sign in':'New here? Create an account'}</button></form></section></main>;
  return <div className="workspace"><aside><div className="brand"><Activity/> FITNESS STUDIO</div><button className="new-chat" onClick={()=>newConversation().catch(e=>setError(e.message))}><Plus size={18}/> New conversation</button>{user.role==='Owner'?<button className="profile-nav" onClick={()=>setPage('approvals')}>Review queue</button>:<button className="profile-nav" onClick={()=>setPage('intake')}>My profile & intake</button>}{user.role==='Member'&&<button className="profile-nav" onClick={()=>setPage('plans')}>My plans</button>}{user.role==='Owner'&&<button className="profile-nav" onClick={()=>setPage('dashboard')}>Studio overview</button>}<button className="profile-nav" onClick={()=>setPage('notifications')}>Updates</button>{user.role==='Owner'&&<button className="profile-nav" onClick={()=>setPage('groups')}>Member groups</button>}{user.role==='Owner'&&<button className="profile-nav" onClick={()=>setPage('incidents')}>Incidents</button>}{user.role==='Owner'&&<button className="profile-nav" onClick={()=>setPage('budgets')}>Budget limits</button>}{user.role==='Owner'&&<button className="profile-nav" onClick={()=>setPage('members')}>Members</button>}{user.role==='Owner'&&<button className="profile-nav" onClick={()=>setPage('versions')}>Agent versions</button>}{user.role==='Owner'&&<button className="profile-nav" onClick={()=>setPage('releases')}>Agent releases</button>}<span className="eyebrow">CONVERSATIONS</span><nav>{sessions.map(session=><button key={session.id} className={selected===session.id?'active':''} onClick={()=>{setPage('chat');setSelected(session.id);}}><MessageSquare size={16}/>{session.title}</button>)}</nav><div className="account"><span className="avatar">{user.display_name.slice(0,1)}</span><div><strong>{user.display_name}</strong><small>{user.role==='Owner'?'Studio owner':'Member workspace'}</small></div><button aria-label="Sign out" onClick={signOut}><LogOut size={18}/></button></div></aside><main className="conversation">{page==='intake'?<Intake/>:page==='approvals'?<Approvals/>:page==='plans'?<Plans/>:page==='dashboard'?<Dashboard/>:page==='notifications'?<Notifications/>:page==='groups'?<Groups/>:page==='incidents'?<Incidents/>:page==='budgets'?<Budgets/>:page==='members'?<Members/>:page==='versions'?<AgentVersions/>:page==='releases'?<Releases/>:<><header><div><span className="eyebrow">YOUR FITNESS JOURNEY</span><h2>{history?.conversation.active_role==='TechnicalManager'?'Studio operations':history?.conversation.active_role==='Nutrition'?'Nutrition guidance':history?.conversation.active_role==='Coach'?'Your training companion':'Meet your intake assistant'}</h2></div><span className="badge"><ShieldCheck size={15}/> Private workspace</span></header>{error&&<p role="alert" className="error banner">{error}</p>}<section className="messages" aria-label="Conversation messages">{!history?.messages.length&&<div className="empty"><span className="hero-icon"><Activity size={34}/></span><h1>Make room for<br/>a stronger you.</h1><p>Tell us about your goals, your routine, and what you would like to work on. You can write in English or Chinese.</p><div className="suggestions">{['I want to build a consistent routine.','Help me get started with my intake.'].map(text=><button key={text} onClick={()=>setDraft(text)}>{text}</button>)}</div></div>}{history?.messages.map(message=><article key={message.id} className={`message ${message.sender==='User'?'sent':'received'}`}><small>{message.sender==='User'?'You':message.sender}</small><p>{message.content}</p>{message.cards?.some(card=>card.type==='IntakeReview')&&<button className="text-button" onClick={()=>setPage('intake')}>Review intake details</button>}{message.cards?.some(card=>['TrainingCandidate','PlanPairReview'].includes(card.type))&&<button className="text-button" onClick={()=>setPage('plans')}>Review plan candidates</button>}</article>)}{history?.requests.filter(task=>task.status!=='Completed').map(task=><div className="task-state" key={task.id}><span className={task.status==='Running'?'pulse':''}/>{task.status==='Stopped'?(task.status_message||'This request stopped. Review its details before submitting a new request.'):task.status==='Running'?'Working on your request…':['Waiting','AwaitingConfirmation'].includes(task.status)?(task.status_message||'Your request is waiting for the next step.'):(task.status_message||'Your request is queued.')}</div>)}</section><form className="composer" onSubmit={send}><div className="composer-top"><label htmlFor="purpose">Request type</label><select id="purpose" value={purpose} onChange={e=>setPurpose(e.target.value)}>{(user.role==='Owner'?['Chat','Operations']:['Chat','NutritionChat','Intake','Training','Nutrition','Combined']).map(item=><option key={item} value={item}>{item==='NutritionChat'?'Nutrition questions':item==='Chat'?'Chat':item}</option>)}</select>{user.role==='Owner'&&<><label htmlFor="operations-group">Agent configuration</label><select id="operations-group" value={operationsGroup} onChange={e=>setOperationsGroup(e.target.value)}><option value="">Default</option>{operationsGroups.filter(group=>group.name!=='Default').map(group=><option key={group.id} value={group.id}>{group.name}</option>)}</select></>}</div><div className="input-row"><textarea aria-label="Message" placeholder="Tell us what you have in mind…" value={draft} onChange={e=>setDraft(e.target.value)} maxLength={12000} rows={2}/><button className="send" aria-label="Send message" disabled={busy||!draft.trim()}><ArrowUp size={22}/></button></div><small>AI guidance supports your routine. It does not replace professional medical care.</small></form></>}</main></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);

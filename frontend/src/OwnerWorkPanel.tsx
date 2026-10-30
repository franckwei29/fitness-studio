import {useEffect,useRef,useState} from 'react';
import {ArrowLeft,ArrowUpRight,BriefcaseBusiness,X} from 'lucide-react';
import {Approvals} from './Approvals';
import {Groups} from './Groups';
import {Members} from './Members';
import {Budgets} from './Budgets';
import {AgentVersions} from './AgentVersions';
import {Releases} from './Releases';
import {Incidents} from './Incidents';
import {Dashboard} from './Dashboard';
import {Requests} from './Requests';
import {OwnerUpdates} from './OwnerUpdates';
import {readRoute, type OwnerTarget} from './ownerNavigation';
const titles:Record<string,string>={approvals:'Review & decide',groups:'Service settings',members:'Member details',budgets:'Usage & spending',versions:'Agent workshop',releases:'Release centre',incidents:'Service recovery',dashboard:'Operational evidence',requests:'Your activity',notifications:'Studio updates'};
export function OwnerWorkPanel({initial,conversationId,onClose,onOpenPage,onChanged,onAnalyze}:{initial:OwnerTarget;conversationId:string;onClose:()=>void;onOpenPage:(target:OwnerTarget)=>void;onChanged:(target?:OwnerTarget)=>Promise<void>;onAnalyze:(id:string)=>Promise<void>}){
 const [trail,setTrail]=useState<OwnerTarget[]>([initial]),[dirty,setDirty]=useState(false),[discard,setDiscard]=useState<(()=>void)|null>(null);
 const dialog=useRef<HTMLDialogElement>(null);const target=trail.at(-1)!;
 const key=JSON.stringify(target);const props={targetId:target.id,targetSection:target.section,onChanged:async(next?:OwnerTarget)=>{setDirty(false);await onChanged(next||target);}};
 const guarded=(action:()=>void)=>{if(dirty)setDiscard(()=>action);else action();};
 function open(next:OwnerTarget){guarded(()=>{setDirty(false);setTrail(t=>[...t,next]);});}
 useEffect(()=>{const before=document.activeElement as HTMLElement|null;dialog.current?.showModal();return()=>before?.focus();},[]);
 useEffect(()=>{const handler=(event:Event)=>open((event as CustomEvent<OwnerTarget>).detail);window.addEventListener('owner-panel-navigate',handler);return()=>window.removeEventListener('owner-panel-navigate',handler);});
 return <dialog ref={dialog} className="owner-work-dialog" aria-label="Studio work panel" data-owner-panel onCancel={event=>{event.preventDefault();guarded(onClose);}}>
 <header className="owner-work-header"><span className="owner-work-icon"><BriefcaseBusiness size={22}/></span><div><span className="eyebrow">IN THIS CONVERSATION</span><h2>{titles[target.page]||'Studio workspace'}</h2></div><button className="text-button" onClick={()=>guarded(()=>onOpenPage(target))}><ArrowUpRight size={17}/>Open page</button><button className="owner-panel-close" aria-label="Close work panel" onClick={()=>guarded(onClose)}><X size={22}/></button></header>
 <div className="owner-work-context">{trail.length>1?<button className="text-button" onClick={()=>guarded(()=>{setDirty(false);setTrail(t=>t.slice(0,-1));})}><ArrowLeft size={15}/>Previous step</button>:<span>Review, make changes, and return to your conversation.</span>}<span className="owner-context-dot"/>Your conversation is saved</div>
 {discard&&<div className="owner-panel-discard" role="alert"><strong>You have unsaved edits.</strong><p>Keep working here, or discard these edits before leaving.</p><div className="action-group"><button className="primary" onClick={()=>setDiscard(null)}>Keep editing</button><button className="danger" onClick={()=>{setDirty(false);const action=discard;setDiscard(null);action();}}>Discard edits</button></div></div>}
 <div className="owner-work-body" key={key} onChangeCapture={()=>setDirty(true)} onClickCapture={event=>{const link=(event.target as HTMLElement).closest('a');const hash=link?.getAttribute('href');if(hash?.startsWith('#/')){event.preventDefault();event.stopPropagation();const next=readRoute(hash);if(next.page==='chat')guarded(onClose);else open(next);}}}>
 {target.page==='approvals'?<Approvals {...props}/>:target.page==='groups'?<Groups {...props}/>:target.page==='members'?<Members {...props}/>:target.page==='budgets'?<Budgets {...props}/>:target.page==='versions'?<AgentVersions {...props}/>:target.page==='releases'?<Releases {...props}/>:target.page==='incidents'?<Incidents {...props} onAnalyze={async id=>{await onAnalyze(id);onClose();}}/>:target.page==='dashboard'?<Dashboard {...props}/>:target.page==='notifications'?<OwnerUpdates targetId={target.id} conversationId={conversationId} onOpen={open} onChanged={onChanged}/>:target.page==='requests'?<Requests {...props} onOpen={()=>guarded(onClose)}/>:<p>This view is available from the studio navigation.</p>}
 </div><footer className="owner-work-footer"><span>Changes use the same reviewed controls as the business pages.</span><button className="secondary" onClick={()=>guarded(onClose)}>Continue conversation<ArrowLeft size={15}/></button></footer>
 </dialog>;
}

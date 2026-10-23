import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import { Check, Clock3 } from 'lucide-react';
import { ProfileHistory } from './ProfileHistory';

type Assessment = {id:string;version:number;status:string;confirmed_facts:Record<string,unknown>;proposed_facts:Record<string,unknown>;decision:null|{outcome:string;reasons:string[]}};
type Profile = {member:null|{status:string;profile_version:number};snapshot:null|{facts:Record<string,unknown>};assessment?:Assessment|null};
const questions:Record<string,string> = {
  prescribed_exercise:'Do you require a prescribed exercise programme?',
  prescribed_nutrition:'Do you require a prescribed nutrition programme?',
  medical_supervision:'Do you currently require medical supervision for exercise or nutrition?',
  pregnancy_postpartum:'Are you pregnant or in a postpartum recovery period?',
  postoperative_acute_injury:'Are you recovering from surgery or an acute injury?',
  resolved_past_injury:'Do you have a resolved past injury that should be reviewed?',
};
const canEdit = (assessment:Assessment|null) => !!assessment && ['Collecting','NeedsInformation','PendingOwnerReview'].includes(assessment.status);

export function Intake(){
  const [assessment,setAssessment]=useState<Assessment|null>(null);
  const [editing,setEditing]=useState<Assessment|null>(null);
  const [profile,setProfile]=useState<Profile|null>(null);
  const [error,setError]=useState(''),[syncError,setSyncError]=useState(''),[busy,setBusy]=useState(false);
  const [loading,setLoading]=useState(true);
  const mutation=useRef(false),generation=useRef(0);
  useEffect(()=>{
    let active=true,reading=false,initial=true;
    async function refresh(){
      if(reading||mutation.current)return;
      reading=true;const current=generation.current;
      try{
        const result=await api<Profile>('/me/profile');
        if(!active||current!==generation.current)return;
        setProfile(result);setAssessment(result.assessment||null);setSyncError('');
        if(initial&&result.assessment&&['Collecting','NeedsInformation'].includes(result.assessment.status))setEditing(result.assessment);
        initial=false;
      }catch{
        if(active&&current===generation.current)setSyncError('Unable to refresh your review status. We will retry automatically.');
      }finally{reading=false;if(active)setLoading(false);}
    }
    void refresh();
    const timer=window.setInterval(()=>{if(document.visibilityState==='visible')void refresh();},5000);
    window.addEventListener('focus',refresh);
    return ()=>{active=false;window.clearInterval(timer);window.removeEventListener('focus',refresh);};
  },[]);
  async function start(){
    if(mutation.current)return;
    mutation.current=true;generation.current++;setBusy(true);setError('');
    try{const result=await api<Assessment>('/assessments',{});setAssessment(result);setEditing(result);}
    catch(e){setError((e as Error).message);}
    finally{mutation.current=false;setBusy(false);}
  }
  const stale=!!editing&&(!assessment||editing.id!==assessment.id||editing.version!==assessment.version||editing.status!==assessment.status);
  async function confirm(event:FormEvent<HTMLFormElement>){
    event.preventDefault();if(!editing||stale||mutation.current)return;
    const fields=new FormData(event.currentTarget);
    const facts:Record<string,unknown>={};
    for(const key of ['age','available_days','session_minutes'])facts[key]=Number(fields.get(key));
    for(const key of ['goal','experience'])facts[key]=String(fields.get(key));
    for(const key of ['equipment','dietary_restrictions'])facts[key]=String(fields.get(key)||'').split(',').map(item=>item.trim()).filter(Boolean);
    for(const key of Object.keys(questions))facts[key]=fields.get(key)==='yes';
    mutation.current=true;generation.current++;setBusy(true);setError('');
    try{
      const result=await api<Assessment>(`/assessments/${editing.id}/confirm`,{expected_version:editing.version,facts});
      setAssessment(result);setEditing(result.status==='NeedsInformation'?result:null);
      try{setProfile(await api<Profile>('/me/profile'));setSyncError('');}
      catch{setSyncError('Your submission was saved. Membership status will refresh automatically.');}
    }catch(e){setError((e as Error).message);}
    finally{mutation.current=false;setBusy(false);}
  }
  const displayed=editing||assessment;
  // Confirmed answers take precedence over earlier suggestions from a conversation.
  const values={...(profile?.snapshot?.facts||{}),...(displayed?.proposed_facts||{}),...(displayed?.confirmed_facts||{})};
  const locked=!editing||busy||stale;
  const pending=assessment?.status==='PendingOwnerReview';
  const accepted=assessment?.status==='Accepted';
  const feedback=<div className="intake-feedback" aria-live="polite">{error&&<p className="error" role="alert">{error}</p>}{syncError&&<p className="muted" role="status">{syncError}</p>}</div>;
  return <section className="intake-page">
    <span className="eyebrow">A FOUNDATION FOR YOUR PLAN</span><h1>Your starting point</h1>
    <p className="muted">Review your goals, availability, and eligibility before personalised planning begins.</p>
    <div className="profile-status"><strong>Membership status</strong><span>{pending?'Awaiting owner review':profile?.member?.status||'Intake not completed'}</span>{profile?.member&&<small>Confirmed profile version {profile.member.profile_version}</small>}</div>
    {loading?<p role="status">Loading your details…</p>:!assessment?<>{feedback}<button className="primary start-intake" disabled={busy||!profile} onClick={start}>{busy?'Opening intake…':profile?.member?'Review or update my profile':'Start my intake'}</button></>:<form onSubmit={confirm} className="intake-form" key={`${displayed?.id}:${displayed?.version}:${!!editing}`}>
      <h2>Goals and routine</h2>
      {editing&&<p className="muted">{pending?'You are editing your submitted details. Submitting changes replaces the previous review.':'Review every answer before confirming.'}</p>}
      <div className="form-grid">
        <label>Age<input disabled={locked} name="age" type="number" min={1} max={120} required defaultValue={String(values.age||'')}/></label>
        <label>Training experience<select disabled={locked} name="experience" required defaultValue={String(values.experience||'')}><option value="" disabled>Select experience</option><option>Beginner</option><option>Intermediate</option><option>Advanced</option></select></label>
        <label>Available days per week<input disabled={locked} name="available_days" type="number" min={1} max={7} required defaultValue={String(values.available_days||'')}/></label>
        <label>Minutes per session<input disabled={locked} name="session_minutes" type="number" min={5} max={240} required defaultValue={String(values.session_minutes||'')}/></label>
      </div>
      <label>Your main goal<input disabled={locked} name="goal" required maxLength={500} defaultValue={String(values.goal||'')} placeholder="For example, build strength and consistency"/></label>
      <label>Available equipment<input disabled={locked} name="equipment" maxLength={1000} defaultValue={Array.isArray(values.equipment)?values.equipment.join(', '):''} placeholder="Separate items with commas; leave blank for no equipment"/></label>
      <label>Dietary restrictions<input disabled={locked} name="dietary_restrictions" maxLength={1000} defaultValue={Array.isArray(values.dietary_restrictions)?values.dietary_restrictions.join(', '):''} placeholder="Separate items with commas; leave blank if none"/></label>
      <h2>Eligibility and review</h2><p className="muted">{editing?'Answer each question explicitly. Some needs require professional support outside this service.':'These are the answers you submitted.'}</p>
      {Object.entries(questions).map(([key,text])=><fieldset disabled={locked} key={key}><legend>{text}</legend>{['yes','no'].map(answer=><label className="radio-label" key={answer}><input type="radio" name={key} value={answer} required defaultChecked={typeof values[key]==='boolean'&&values[key]===(answer==='yes')}/>{answer==='yes'?'Yes':'No'}</label>)}</fieldset>)}
      <div className="intake-submission">
        {!editing&&pending&&<div className="submission-receipt" role="status"><span className="receipt-icon" aria-hidden="true"><Clock3 size={22}/></span><div><strong>Submitted — awaiting owner review</strong><p>Your details have been saved. You do not need to submit again. This page will update after the owner reviews them.</p></div></div>}
        {!editing&&accepted&&<div className="submission-receipt" role="status"><span className="receipt-icon" aria-hidden="true"><Check size={22}/></span><div><strong>Approved</strong><p>Your confirmed profile is ready for planning.</p></div></div>}
        {assessment.status==='NeedsInformation'&&<p role="status">Some required information is missing or invalid. Review your answers before submitting.</p>}
        {!editing&&assessment.status==='Collecting'&&<p role="status">Your details need to be completed or revised before review.</p>}
        {!editing&&assessment.status==='Rejected'&&<p role="status">Your submission was not approved. You can review your details and start a new submission.</p>}
        {!editing&&assessment.status==='ReferredOut'&&<p role="status">Your needs are outside this service’s scope. Please seek appropriate professional guidance.</p>}
        {stale&&<p role="alert">Your review status changed while you were editing. Review the latest details before making further changes.</p>}
        {feedback}
        {editing&&!stale&&<><label className="confirmation"><input type="checkbox" disabled={busy} required/> I have reviewed these details and confirm they are accurate.</label><div className="intake-actions"><button className="primary" disabled={busy}>{busy?'Submitting your details…':pending?'Submit updated details':'Confirm my details'}</button>{pending&&<button type="button" disabled={busy} onClick={()=>{setEditing(null);setError('');}}>Cancel editing</button>}</div></>}
        {stale&&<button type="button" onClick={()=>{setEditing(null);setError('');}}>Review latest details</button>}
        {!editing&&(canEdit(assessment)?<button type="button" onClick={()=>{setEditing(assessment);setError('');}}>{pending?'Edit submitted details':'Edit my details'}</button>:<button type="button" disabled={busy} onClick={start}>{busy?'Opening intake…':'Review or update my profile'}</button>)}
      </div>
    </form>}
    {profile?.member&&<ProfileHistory key={profile.member.profile_version}/>}
  </section>;
}

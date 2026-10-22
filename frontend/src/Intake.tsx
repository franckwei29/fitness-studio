import { useEffect, useState, type FormEvent } from 'react';
import { api } from './api';
import { ProfileHistory } from './ProfileHistory';

type Assessment = {id:string;version:number;status:string;confirmed_facts:Record<string,unknown>;proposed_facts:Record<string,unknown>;decision:null|{outcome:string;reasons:string[]}};
type Profile = {member:null|{status:string;profile_version:number};snapshot:null|{facts:Record<string,unknown>}};
const questions:Record<string,string> = {
  prescribed_exercise:'Do you require a prescribed exercise programme?',
  prescribed_nutrition:'Do you require a prescribed nutrition programme?',
  medical_supervision:'Do you currently require medical supervision for exercise or nutrition?',
  pregnancy_postpartum:'Are you pregnant or in a postpartum recovery period?',
  postoperative_acute_injury:'Are you recovering from surgery or an acute injury?',
  resolved_past_injury:'Do you have a resolved past injury that should be reviewed?',
};
export function Intake(){
  const [assessment,setAssessment]=useState<Assessment|null>(null);
  const [profile,setProfile]=useState<Profile|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{api<Profile>('/me/profile').then(setProfile).catch(e=>setError(e.message));},[]);
  async function start(){setBusy(true);setError('');try{setAssessment(await api<Assessment>('/assessments',{}));}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  async function confirm(event:FormEvent<HTMLFormElement>){
    event.preventDefault();if(!assessment)return;setBusy(true);setError('');
    const fields=new FormData(event.currentTarget);
    const facts:Record<string,unknown>={};
    for(const key of ['age','available_days','session_minutes']) facts[key]=Number(fields.get(key));
    for(const key of ['goal','experience']) facts[key]=String(fields.get(key));
    for(const key of ['equipment','dietary_restrictions']) facts[key]=String(fields.get(key)||'').split(',').map(item=>item.trim()).filter(Boolean);
    for(const key of Object.keys(questions)) facts[key]=fields.get(key)==='yes';
    try{const result=await api<Assessment>(`/assessments/${assessment.id}/confirm`,{expected_version:assessment.version,facts});setAssessment(result);setProfile(await api<Profile>('/me/profile'));}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  const values={...(profile?.snapshot?.facts||{}),...(assessment?.confirmed_facts||{}),...(assessment?.proposed_facts||{})};
  const editable=assessment&&['Collecting','NeedsInformation','PendingOwnerReview'].includes(assessment.status);
  return <section className="intake-page"><span className="eyebrow">A FOUNDATION FOR YOUR PLAN</span><h1>Your starting point</h1><p className="muted">Review your goals, availability, and eligibility before personalised planning begins.</p>{error&&<p className="error" role="alert">{error}</p>}<div className="profile-status"><strong>Membership status</strong><span>{profile?.member?.status||'Intake not completed'}</span>{profile?.member&&<small>Confirmed profile version {profile.member.profile_version}</small>}</div>{assessment&&<div className="assessment-status" role="status"><strong>{assessment.status.replace(/([a-z])([A-Z])/g,'$1 $2')}</strong><p>{assessment.status==='Accepted'?'Your confirmed profile is ready for planning.':assessment.status==='PendingOwnerReview'?'Your application is waiting for an owner review.':assessment.status==='ReferredOut'?'Your needs are outside this service’s scope. Please seek appropriate professional guidance.':assessment.status==='NeedsInformation'?'Some required information is missing or invalid. Review your answers below.':'Complete the details below and confirm they are accurate.'}</p></div>}{!editable?<button className="primary start-intake" disabled={busy} onClick={start}>{busy?'Opening intake…':profile?.member?'Review or update my profile':'Start my intake'}</button>:<form onSubmit={confirm} className="intake-form" key={`${assessment.id}:${assessment.version}`}><h2>Goals and routine</h2>{Object.keys(assessment.proposed_facts||{}).length>0&&<p className="muted">Some fields were suggested from your conversation. Review every answer before confirming.</p>}<div className="form-grid"><label>Age<input name="age" type="number" min={1} max={120} required defaultValue={String(values.age||'')}/></label><label>Training experience<select name="experience" required defaultValue={String(values.experience||'')}><option value="" disabled>Select experience</option><option>Beginner</option><option>Intermediate</option><option>Advanced</option></select></label><label>Available days per week<input name="available_days" type="number" min={1} max={7} required defaultValue={String(values.available_days||'')}/></label><label>Minutes per session<input name="session_minutes" type="number" min={5} max={240} required defaultValue={String(values.session_minutes||'')}/></label></div><label>Your main goal<input name="goal" required maxLength={500} defaultValue={String(values.goal||'')} placeholder="For example, build strength and consistency"/></label><label>Available equipment<input name="equipment" maxLength={1000} defaultValue={Array.isArray(values.equipment)?values.equipment.join(', '):''} placeholder="Separate items with commas; leave blank for no equipment"/></label><label>Dietary restrictions<input name="dietary_restrictions" maxLength={1000} defaultValue={Array.isArray(values.dietary_restrictions)?values.dietary_restrictions.join(', '):''} placeholder="Separate items with commas; leave blank if none"/></label><h2>Eligibility and review</h2><p className="muted">Answer each question explicitly. Some needs require professional support outside this service.</p>{Object.entries(questions).map(([key,text])=><fieldset key={key}><legend>{text}</legend>{['yes','no'].map(answer=><label className="radio-label" key={answer}><input type="radio" name={key} value={answer} required defaultChecked={typeof values[key]==='boolean'&&values[key]===(answer==='yes')}/>{answer==='yes'?'Yes':'No'}</label>)}</fieldset>)}<label className="confirmation"><input type="checkbox" required/> I have reviewed these details and confirm they are accurate.</label><button className="primary" disabled={busy}>{busy?'Checking your details…':'Confirm my details'}</button></form>}{profile?.member&&<ProfileHistory key={profile.member.profile_version}/>}</section>;
}

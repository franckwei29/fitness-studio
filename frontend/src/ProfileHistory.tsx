import { useState } from 'react';
import { api } from './api';

type Change={field:string;before_present:boolean;after_present:boolean;before:unknown;after:unknown};
type Version={version:number;created_at:string;facts:Record<string,unknown>;changes:Change[];source_assessment_id:string|null};
type History={current_version:number|null;versions:Version[]};
const label=(key:string)=>key.replaceAll('_',' ').replace(/^./,letter=>letter.toUpperCase());
const display=(value:unknown,present=true):string=>!present?'Not recorded':value===null?'Unknown':typeof value==='boolean'?(value?'Yes':'No'):Array.isArray(value)?value.map(item=>String(item)).join(', ')||'None':String(value);

export function ProfileHistory(){
  const [open,setOpen]=useState(false),[data,setData]=useState<History|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function toggle(){
    if(open){setOpen(false);return;}
    setOpen(true);setBusy(true);setError('');
    try{setData(await api<History>('/me/profile/history'));}catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  return <section aria-label="Profile history"><button className="text-button" disabled={busy} onClick={toggle}>{open?'Hide profile history':'View profile history'}</button>
    {open&&<><h2>Your confirmed profile history</h2><p className="muted">These are saved confirmations. Viewing an earlier version does not restore it or change your current plans. To update your details, use the profile review form.</p>
      {busy&&<p>Loading profile history…</p>}{error&&<p role="alert" className="error">{error}</p>}
      {data?.versions.map(row=><details className="plan-card" key={row.version}><summary>Version {row.version}{row.version===data.current_version?' · Current confirmed profile':''} · {new Date(`${row.created_at}Z`).toLocaleString('en-AU')}</summary>
        <p>{row.source_assessment_id?'Confirmed through an intake assessment.':'Saved confirmed profile.'}</p>
        <h3>{row.version===1?'Initial recorded fields':'Changes from the previous version'}</h3>
        {!row.changes.length?<p>No field values changed.</p>:<div className="table-scroll"><table><thead><tr><th>Field</th><th>Previous</th><th>Confirmed</th></tr></thead><tbody>{row.changes.map(change=><tr key={change.field}><th scope="row">{label(change.field)}</th><td>{display(change.before,change.before_present)}</td><td>{display(change.after,change.after_present)}</td></tr>)}</tbody></table></div>}
        <details><summary>All confirmed fields in this version</summary><dl>{Object.entries(row.facts).map(([key,value])=><div key={key}><dt>{label(key)}</dt><dd>{display(value)}</dd></div>)}</dl></details>
      </details>)}
      {data&&!data.versions.length&&<p>No confirmed profile versions yet.</p>}
    </>}
  </section>;
}

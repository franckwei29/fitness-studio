import { useState, type FormEvent } from 'react';
import { api } from './api';

type ReviewResult = {id:string;agent_version_id:string;case_key:string;output:unknown};

export function EvaluationReview({candidateId,runId,results,onReviewed}:{candidateId:string;runId:string;results:ReviewResult[];onReviewed:()=>Promise<void>}){
 const [busy,setBusy]=useState(false),[error,setError]=useState('');
 async function submit(event:FormEvent<HTMLFormElement>){
  event.preventDefault();setBusy(true);setError('');
  const fields=new FormData(event.currentTarget);
  const ratings=results.map(result=>({result_id:result.id,explanation_quality:Number(fields.get(`${result.id}:quality`)),factual_and_role_compliant:fields.get(`${result.id}:compliance`)==='yes',notes:String(fields.get(`${result.id}:notes`)||'').trim()}));
  try{await api(`/owner/agent-versions/${candidateId}/evaluations/${runId}/review`,{ratings});await onReviewed();}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 return <form className="intake-form" onSubmit={submit}><h3>Review explanation quality</h3><p className="muted">Inspect every baseline and candidate response. A score of at least 3 and a positive factual and role-boundary assessment are required for every result. This review does not deploy a version.</p>{error&&<p role="alert" className="error">{error}</p>}{results.map(result=><fieldset key={result.id}><legend>{result.agent_version_id===candidateId?'Candidate':'Baseline'} · {result.case_key}</legend><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify(result.output,null,2)}</pre><label>Explanation quality<select name={`${result.id}:quality`} required defaultValue=""><option value="" disabled>Select a score</option><option value="1">1 — Unusable</option><option value="2">2 — Major issues</option><option value="3">3 — Adequate</option><option value="4">4 — Clear and useful</option><option value="5">5 — Excellent</option></select></label><label>Is the response factual and within the agent's role?<select name={`${result.id}:compliance`} required defaultValue=""><option value="" disabled>Select an assessment</option><option value="yes">Yes</option><option value="no">No</option></select></label><label>Review notes<textarea name={`${result.id}:notes`} required minLength={10} maxLength={2000} rows={3} placeholder="Explain your assessment, including unsupported claims or missing limitations."/></label></fieldset>)}<button className="primary" disabled={busy||!results.length}>{busy?'Verifying evidence…':'Submit quality review'}</button></form>;
}

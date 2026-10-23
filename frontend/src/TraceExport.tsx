import type { OwnerTarget } from './ownerNavigation';
import { useState } from 'react';
import { api } from './api';

export function TraceExport({id,fingerprint,configured,onChanged}:{id:string;fingerprint:string;configured:boolean;onChanged?:(target?:OwnerTarget)=>void}){
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const [result,setResult]=useState<{status:string;run_id:string;message:string}|null>(null);
  async function download(){
    setBusy(true);setError('');
    try{
      const response=await fetch(`/api/owner/requests/${id}/trace/export`,{credentials:'same-origin'});
      if(!response.ok||!response.headers.get('content-type')?.startsWith('application/json'))throw Error('Trace download failed. Refresh your session and try again.');
      const url=URL.createObjectURL(await response.blob());const link=document.createElement('a');
      link.href=url;link.download=`trace-${id}.json`;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function publish(){setBusy(true);setError('');try{
    setResult(await api(`/owner/requests/${id}/trace/langsmith`,{fingerprint}));onChanged?.({page:'dashboard',id,section:'trace'});
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <section aria-label="Trace export"><h3>Export operational evidence</h3>
    <p className="muted">The snapshot includes role, status, version references, token counts, and costs. Personal profile fields, conversation text, prompts, model output, credentials, and free-text diagnostic details are omitted.</p>
    <button className="text-button" disabled={busy} onClick={download}>Download redacted trace (.json)</button>
    <button className="text-button" disabled={busy||!configured||!fingerprint} onClick={publish}>Export snapshot to LangSmith</button>
    {!configured&&<p className="muted">External tracing is not configured. Local download remains available.</p>}
    {error&&<p role="alert" className="error">{error}</p>}
    {result&&<div role="status"><p>{result.status}: {result.message}</p><p>Run ID: <code>{result.run_id}</code></p></div>}
  </section>;
}

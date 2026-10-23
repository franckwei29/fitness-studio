import { readRoute, routeHash, type OwnerTarget } from './ownerNavigation';
import { useEffect, useRef, useState } from 'react';
import { api } from './api';

export type Approval = {
  id: string; subject_kind: string; subject_id: string; subject_version: number;
  fingerprint: string; decision: string; created_at: string; expires_at: string;
  context: { facts?: Record<string, unknown>; baseline?: unknown; candidate?: unknown;
    evaluation?: unknown; training?: unknown; nutrition?: unknown; validation?: unknown };
};
const label = (value: string) => value.replace(/([a-z])([A-Z])/g, '$1 $2');

export function ApprovalReview({ approval, onChanged, onClose }: {
  approval: Approval; onChanged?: (target?: OwnerTarget) => void; onClose?: () => void;
}) {
  const [current, setCurrent] = useState(approval), [comment, setComment] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(''), [notice, setNotice] = useState('');
  const submitting = useRef(false);
  const draftKey = `owner-review-draft:${approval.id}:${approval.fingerprint}`;
  const previousDraftKey = useRef(draftKey);
  useEffect(() => {
    if (previousDraftKey.current !== draftKey) sessionStorage.removeItem(previousDraftKey.current);
    previousDraftKey.current = draftKey; setCurrent(approval);
    setComment(approval.decision === 'Pending' ? sessionStorage.getItem(draftKey) || '' : ''); setNotice(''); setError('');
  }, [approval.id, approval.fingerprint]);
  useEffect(() => { setCurrent(value => ({...value, decision: approval.decision})); if (approval.decision !== 'Pending') { sessionStorage.removeItem(draftKey); setComment(''); } }, [approval.decision, draftKey]);
  async function decide(decision: string) {
    if (submitting.current || current.decision !== 'Pending') return;
    submitting.current = true; setBusy(decision); setError(''); setNotice('');
    try {
      const path = current.subject_kind === 'AgentRelease' ? `/owner/releases/${current.subject_id}/review`
        : current.subject_kind === 'GroupConfiguration' ? `/owner/changes/${current.subject_id}/review`
          : `/owner/approvals/${current.id}/decisions`;
      await api(path, { decision, fingerprint: current.fingerprint, comment });
      setCurrent({ ...current, decision }); setComment(''); sessionStorage.removeItem(draftKey);
      setNotice(decision === 'Approved' ? 'Approval recorded.' : decision === 'Revise' ? 'Revision requested.' : 'Rejection recorded.');
      onChanged?.({page:'approvals',id:current.id});
    } catch (e) {
      setError((e as Error).message);
      try { const latest = (await api<Approval[]>('/owner/approvals')).find(row => row.id === current.id); if (latest) setCurrent(latest); } catch { /* Keep the original failure visible. */ }
    } finally { setBusy(''); submitting.current = false; }
  }
  const expired = !!current.expires_at && new Date(`${current.expires_at.replace(/Z$/, '')}Z`).getTime() <= Date.now();
  return <article className="review-card" aria-label="Approval details">
    {onClose && <button className="text-button" onClick={onClose}>Back to queue</button>}
    <h2>{label(current.subject_kind)}</h2><p className="muted">Version {current.subject_version} · {current.decision}</p>
    <dl>{Object.entries(current.context.facts || {}).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{typeof value === 'boolean' ? (value ? 'Yes' : 'No') : Array.isArray(value) ? value.join(', ') || 'None' : String(value)}</dd></div>)}</dl>
    {['PlanPair', 'TrainingPlan', 'NutritionPlan'].includes(current.subject_kind) && <>
      {current.context.training != null && <><h3>Training candidate</h3><pre>{JSON.stringify(current.context.training, null, 2)}</pre></>}
      {current.context.nutrition != null && <><h3>Nutrition candidate</h3><pre>{JSON.stringify(current.context.nutrition, null, 2)}</pre></>}
      <h3>Validation</h3><pre>{JSON.stringify(current.context.validation, null, 2)}</pre>
    </>}
    {['GroupConfiguration', 'AgentRelease'].includes(current.subject_kind) && <>
      <h3>Current configuration</h3><pre>{JSON.stringify(current.context.baseline, null, 2)}</pre>
      <h3>Proposed configuration</h3><pre>{JSON.stringify(current.context.candidate, null, 2)}</pre>
      <h3>Validation</h3><pre>{JSON.stringify(current.context.evaluation, null, 2)}</pre>
    </>}
    {error && <p role="alert" className="error">{error}</p>}
    {notice && <p role="status" className="assessment-status">{notice}{['GroupConfiguration', 'AgentRelease'].includes(current.subject_kind) && current.decision === 'Approved' ? ' Execution is a separate step; the configuration has not been applied by this approval.' : ''}</p>}
    {current.decision === 'Approved' && current.subject_kind === 'GroupConfiguration' && <a className="text-button" href={routeHash({page:'groups',id:current.subject_id,section:'change'},readRoute().conversation)}>Open approved configuration change</a>}
    {current.decision === 'Approved' && current.subject_kind === 'AgentRelease' && <a className="text-button" href={routeHash({page:'releases',id:current.subject_id},readRoute().conversation)}>Open approved release</a>}
    {current.decision === 'Pending' && !expired && <><label className="review-comment">Decision notes<textarea value={comment} onChange={e => { setComment(e.target.value); sessionStorage.setItem(draftKey, e.target.value); }} rows={3} maxLength={3000} /></label><div className="review-actions">{[['Approved', 'Approve'], ['Revise', 'Request revision'], ['Rejected', 'Reject']].map(([value, text]) => <button key={value} disabled={!!busy} onClick={() => decide(value)}>{busy === value ? 'Saving…' : text}</button>)}</div></>}
    {current.decision === 'Pending' && expired && <p role="status">This review has expired. A new submission is required before a decision can be recorded.</p>}
  </article>;
}

export function Approvals({ targetId, onChanged, embedded = false }: { targetId?: string; targetSection?: string; onChanged?: (target?: OwnerTarget) => void; embedded?: boolean }) {
  const [rows, setRows] = useState<Approval[]>([]), [error, setError] = useState('');
  const [selected, setSelected] = useState<Approval | null>(null), [loading, setLoading] = useState(true), [missing, setMissing] = useState(false);
  async function load(preferredId = targetId) {
    const next = await api<Approval[]>('/owner/approvals'); setRows(next);
    if (preferredId) { const exact = next.find(row => row.id === preferredId); setSelected(exact || null); setMissing(!exact); }
    setLoading(false);
  }
  useEffect(() => { let active = true; setLoading(true); setSelected(null); setMissing(false); setError(''); api<Approval[]>('/owner/approvals').then(next => { if (!active) return; setRows(next); const exact = next.find(row => row.id === targetId); setSelected(exact || null); setMissing(!!targetId && !exact); }).catch(e => { if (active) { setError(e.message); setMissing(!!targetId); } }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [targetId]);
  return <section className={embedded ? 'owner-approval-detail' : 'intake-page'}>
    {!embedded && <><span className="eyebrow">STUDIO OVERSIGHT</span><h1>Review queue</h1><p className="muted">Review the exact submitted information before making a decision. Changes to the application require a new review.</p></>}
    {error && <p role="alert" className="error">{error}</p>}
    <button className="text-button" onClick={() => load(selected?.id || targetId).catch(e => setError(e.message))}>Refresh queue</button>
    {loading ? <p role="status">Loading review…</p> : missing ? <p role="alert" className="error">This review is unavailable. It may have been removed or you may no longer have access. No other review has been opened.</p> : selected ? <ApprovalReview key={selected.id} approval={selected} onClose={embedded ? undefined : () => setSelected(null)} onChanged={() => { onChanged?.({page:'approvals',id:selected.id}); load(selected.id).catch(e => setError(e.message)); }} /> : <div className="review-list">{!rows.length ? <p className="muted">No approvals have been submitted.</p> : rows.map(row => <button key={row.id} onClick={() => setSelected(row)}><span><strong>{label(row.subject_kind)}</strong><small>Version {row.subject_version} · {new Date(`${row.created_at}Z`).toLocaleString('en-AU')}</small></span><span className="badge">{row.decision}</span></button>)}</div>}
  </section>;
}

import type { OwnerTarget } from './ownerNavigation';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
import { UnknownUsage } from './UnknownUsage';
import { ModelUsage } from './ModelUsage';
type Budget = { id: string; scope_key: string; limit_micros: number; spent_micros: number; reserved_micros: number; fingerprint: string };
export function Budgets({ targetId, targetSection, onChanged }: { targetId?: string; targetSection?: string; onChanged?: (target?: OwnerTarget) => void }) {
  const [rows, setRows] = useState<Budget[]>([]), [scope, setScope] = useState('global'), [amount, setAmount] = useState('0');
  const [reason, setReason] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [missing, setMissing] = useState(false);
  const submitting = useRef(false), unknownOnly = targetSection === 'unknown-usage';
  useEffect(() => {
    let active = true; setLoading(true); setMissing(false); setError(''); setNotice('');
    api<Budget[]>('/owner/budgets').then(next => { if (!active) return; setRows(next); const exact = next.find(row => targetId && !unknownOnly ? row.id === targetId || row.scope_key === targetId : row.scope_key === 'global'); setMissing(!!targetId && !unknownOnly && !exact); setScope(exact?.scope_key || 'global'); setAmount(String((exact?.limit_micros || 0) / 1000000)); setReason(''); }).catch(e => { if (active) { setError(e.message); setMissing(true); } }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [targetId, unknownOnly]);
  function choose(value: string) { setScope(value); const row = rows.find(item => item.scope_key === value); setAmount(String((row?.limit_micros || 0) / 1000000)); setNotice(''); }
  async function submit(event: FormEvent) {
    event.preventDefault(); if (submitting.current) return; submitting.current = true; setBusy(true); setError(''); setNotice('');
    try { const value = Number(amount); if (!Number.isFinite(value) || value < 0 || value > 1000) throw new Error('Enter a limit between US$0 and US$1,000.'); const saved=await api<Budget>('/owner/budgets', { scope_key: scope, limit_micros: Math.round(value * 1000000), expected_fingerprint: rows.find(row => row.scope_key === scope)?.fingerprint || null, reason }); setRows(previous=>[...previous.filter(row=>row.id!==saved.id),saved]);setReason('');setNotice('Budget limit saved. Existing spending and reserved funds are unchanged.');onChanged?.({page:'budgets',id:scope});setRows(await api<Budget[]>('/owner/budgets')); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); submitting.current = false; }
  }
  return <section className="intake-page"><span className="eyebrow">MODEL SPENDING CONTROLS</span><h1>Budget limits</h1><p className="muted">Limits are cumulative account allowances in USD. Reserved funds remain held until usage is reconciled. All applicable accounts must have enough available allowance before a model call.</p>
    {!targetId && <ModelUsage/>}
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status" className="assessment-status">{notice}</p>}
    {loading ? <p role="status">Loading budget details…</p> : missing ? <p role="alert" className="error">This budget account is unavailable. No other account has been selected.</p> : !unknownOnly && <>
      <form className="intake-form plan-card" onSubmit={submit}><label>Budget scope<input list="budget-scopes" value={scope} onChange={e => choose(e.target.value)} required /><datalist id="budget-scopes">{Array.from(new Set(['global', 'role:Intake', 'role:Coach', 'role:Nutrition', 'role:TechnicalManager', ...rows.map(row => row.scope_key)])).map(value => <option key={value} value={value} />)}</datalist></label><p className="muted">Use global, role:RoleName, group:GroupID, or member:MemberID.</p><label>Total limit (USD)<input type="number" min="0" max="1000" step="0.000001" required value={amount} onChange={e => setAmount(e.target.value)} /></label><label>Reason<input required maxLength={1000} value={reason} onChange={e => setReason(e.target.value)} /></label><button className="primary" disabled={busy}>{busy ? 'Saving limit…' : 'Save budget limit'}</button></form>
      <div className="review-list">{rows.map(row => <button key={row.id} onClick={() => choose(row.scope_key)}><span><strong>{row.scope_key}</strong><small>Spent ${(row.spent_micros / 1000000).toFixed(6)} · Reserved ${(row.reserved_micros / 1000000).toFixed(6)}</small></span><span>Limit ${(row.limit_micros / 1000000).toFixed(2)}</span></button>)}</div>
    </>}
    {!missing && <UnknownUsage targetId={unknownOnly ? targetId : undefined} onChanged={onChanged} />}
  </section>;
}

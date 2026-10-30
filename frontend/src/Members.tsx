import { AllowanceDetail } from './CallLimits';
import type { OwnerTarget } from './ownerNavigation';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from './api';
type Member = { id: string; display_name: string; status: string; group_id: string; profile_version: number; allowance?: {limit:number;source:string} };
type Group = { id: string; name: string };
export function Members({ targetId, onChanged }: { targetId?: string; targetSection?: string; onChanged?: (target?: OwnerTarget) => void }) {
  const [members, setMembers] = useState<Member[]>([]), [groups, setGroups] = useState<Group[]>([]), [selected, setSelected] = useState<Member | null>(null);
  const [group, setGroup] = useState(''), [reason, setReason] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const submitting = useRef(false);
  function choose(member: Member) { setSelected(member); setGroup(member.group_id); setReason(''); setNotice(''); }
  useEffect(() => {
    let active = true; setSelected(null); setError(''); setNotice(''); setLoading(true);
    Promise.all([api<Member[]>('/owner/members'), api<Group[]>('/owner/groups')]).then(async ([m, g]) => {
      let exact = targetId ? m.find(row => row.id === targetId) : undefined;
      if (targetId && !exact) exact = await api<Member>(`/owner/members/${encodeURIComponent(targetId)}`);
      if (!active) return; setMembers(m); setGroups(g); if (exact) choose(exact);
    }).catch(e => { if (active) setError(targetId ? `This member is unavailable. ${e.message}` : e.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [targetId]);
  async function save(event: FormEvent) {
    event.preventDefault(); if (!selected || submitting.current) return; submitting.current = true; setBusy(true); setError(''); setNotice('');
    try {
      const saved = await api<{group_id:string;status:string}>(`/owner/members/${selected.id}/group`, { group_id: group, expected_group_id: selected.group_id, reason });
      setSelected({...selected,group_id:saved.group_id,status:saved.status});setGroup(saved.group_id);setReason('');setNotice('Member group updated. Existing work will use the required policy checks.');onChanged?.({page:'members',id:selected.id});
      const next = await api<Member[]>('/owner/members'); setMembers(next);
      const updated = next.find(row => row.id === selected.id) || await api<Member>(`/owner/members/${selected.id}`);
      choose(updated); setNotice('Member group updated. Existing work will use the required policy checks.');
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); submitting.current = false; }
  }
  return <section className="intake-page"><span className="eyebrow">MEMBER SERVICES</span><h1>Members</h1><p className="muted">Manage service groups. Moving a member requires their queued work and existing plan context to be checked again.</p>
    {error && <p role="alert" className="error">{error}</p>}{notice && <p role="status" className="assessment-status">{notice}</p>}
    {loading ? <p role="status">Loading member details…</p> : selected ? <form className="intake-form plan-card" onSubmit={save}>
      <h2>{selected.display_name}</h2><p className="muted">{selected.status} · Profile context version {selected.profile_version}</p>
      <label>Service group<select value={group} onChange={e => setGroup(e.target.value)}>{groups.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
      <p className="muted">Daily AI allowance: {selected.allowance?.limit ?? 'See AI settings'} · {selected.allowance?.source}. A personal exception is retained when changing groups. Usage is not reset.</p><label>Reason for reassignment<input value={reason} onChange={e => setReason(e.target.value)} required maxLength={1000} /></label>
      <button className="primary" disabled={busy || group === selected.group_id}>{busy ? 'Saving assignment…' : 'Update member group'}</button><button className="text-button" type="button" onClick={() => setSelected(null)}>Back to members</button>
    </form> : targetId && error ? null : <div className="review-list">{!members.length ? <p className="muted">No accepted member profiles have been created.</p> : members.map(member => <button key={member.id} onClick={() => choose(member)}><span><strong>{member.display_name}</strong><small>{groups.find(item => item.id === member.group_id)?.name || 'Member group'} · Context version {member.profile_version}</small></span><span className="badge">{member.status}</span></button>)}</div>}
    {selected&&<AllowanceDetail key={`${selected.id}:${selected.group_id}`} scope={`member:${selected.id}`} onChanged={onChanged}/>}
  </section>;
}

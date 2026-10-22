import { useState, type FormEvent } from 'react';

type Cost = { calls: number; settled_micros: number; held_micros: number; unknown_calls: number };
export type Period = {
  start: string; end: string; generated_at: string; new_members: number;
  plan_versions_created: number; successful_activations: number;
  active_members_now: number; unresolved_incidents_now: number;
  settled_micros: number; held_micros: number; unknown_calls: number;
  cost_by_role: (Cost & { role: string })[];
  cost_by_member: (Cost & { member_id: string })[];
  definitions: string[];
};
const money = (value: number) => `USD ${(value / 1_000_000).toFixed(6)}`;

export function OperationPeriod({ data, load }: { data: Period; load: (start: string, end: string) => Promise<void> }) {
  const [start, setStart] = useState(data.start), [end, setEnd] = useState(data.end);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function apply(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await load(start, end); } catch (error) { setError((error as Error).message); } finally { setBusy(false); }
  }
  const query = new URLSearchParams({ start: data.start, end: data.end });
  async function downloadBrief() {
    setBusy(true); setError('');
    try {
      const response = await fetch(`/api/owner/operations-brief?${query}`, { credentials: 'same-origin' });
      if (!response.ok || !response.headers.get('content-type')?.startsWith('text/markdown')) {
        throw new Error('The operations brief could not be downloaded. Refresh the page and check your session.');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url; link.download = `operations-${data.start}-${data.end}.md`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { setError((error as Error).message); } finally { setBusy(false); }
  }
  return <section aria-label="Period summary">
    <h2>Reporting period</h2>
    <form onSubmit={apply} className="profile-grid">
      <label>Period start (UTC)<input type="date" value={start} required onChange={event => setStart(event.target.value)} /></label>
      <label>Period end (UTC)<input type="date" value={end} required min={start} onChange={event => setEnd(event.target.value)} /></label>
      <button className="primary" disabled={busy}>{busy ? 'Loading period…' : 'Apply period'}</button>
    </form>
    {error && <p role="alert" className="error">{error}</p>}
    <p>Showing {data.start} through {data.end} (UTC). The default is the latest seven days.</p>
    <button className="text-button" disabled={busy} onClick={downloadBrief}>Download operations brief (.md)</button>
    <div className="metric-grid">{[
      ['New membership records', data.new_members], ['Plan versions created', data.plan_versions_created],
      ['Successful activations', data.successful_activations], ['Active members now', data.active_members_now],
      ['Unresolved incidents now', data.unresolved_incidents_now], ['Settled cost in period', money(data.settled_micros)],
      ['Held reservations in period', money(data.held_micros)], ['Unknown usage in period', data.unknown_calls],
    ].map(([label, value]) => <div className="metric" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
    <h3>Settled costs by role and member</h3>
    <p className="muted">These are two views of the same calls. Do not add the two breakdowns together.</p>
    {([
      ['Role', data.cost_by_role.map(row => ({ ...row, reference: row.role }))],
      ['Member reference', data.cost_by_member.map(row => ({ ...row, reference: row.member_id }))],
    ] as [string, (Cost & { reference: string })[]][]).map(([label, rows]) => <div className="table-scroll" key={label}><table>
      <thead><tr><th>{label}</th><th>Call records</th><th>Settled</th><th>Held</th><th>Unknown</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.reference}><td>{row.reference}</td><td>{row.calls}</td><td>{money(row.settled_micros)}</td><td>{money(row.held_micros)}</td><td>{row.unknown_calls}</td></tr>)}</tbody>
    </table>{!rows.length && <p>No call records in this period.</p>}</div>)}
    <details><summary>Metric definitions</summary><ul>{data.definitions.map(text => <li key={text}>{text}</li>)}</ul></details>
  </section>;
}

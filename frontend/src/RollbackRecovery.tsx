import { useState, type FormEvent } from 'react';
import { api } from './api';

export function RollbackRecovery({ releaseId, groupVersion }: { releaseId: string; groupVersion?: number }) {
  const [reason, setReason] = useState(''), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [submitted, setSubmitted] = useState(false);
  async function propose(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      await api(`/owner/releases/${releaseId}/recovery-proposals`, { expected_group_version: groupVersion, reason });
      setSubmitted(true);
    } catch (error) { setError((error as Error).message); } finally { setBusy(false); }
  }
  return <section aria-label="Blocked rollback recovery">
    <h4>Recover against the current configuration</h4>
    <p>The affected role is blocked while it still uses this failed version. Propose restoring its retained version while preserving other current group settings. This requires a new review.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {submitted ? <p className="assessment-status">Recovery proposal saved. Review it in Review queue, then execute the approved change in Member groups. Interrupted tasks require separate recovery verification.</p> : <form className="intake-form" onSubmit={propose}>
      <p>Current group configuration version: {groupVersion ?? 'Unavailable; refresh releases'}</p>
      <label>Reason for configuration recovery<textarea required minLength={10} maxLength={3000} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <button className="primary" disabled={busy || !groupVersion || reason.trim().length < 10}>Submit recovery for approval</button>
    </form>}
  </section>;
}

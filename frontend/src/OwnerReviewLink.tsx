import { useEffect, useState } from 'react';
import { api } from './api';
import { readRoute, routeHash } from './ownerNavigation';

type ReviewReference = { id: string; subject_id: string; subject_kind: string; decision: string };
export function useOwnerReviewLinks(active: boolean, revision: string) {
  const [reviews, setReviews] = useState<ReviewReference[]>([]), [error, setError] = useState(''), [loading, setLoading] = useState(false);
  useEffect(() => {
    let current = true; setError('');
    if (!active) { setReviews([]); setLoading(false); return; }
    setLoading(true);
    api<ReviewReference[]>('/owner/approvals').then(value => { if (current) setReviews(value); }).catch(e => { if (current) setError(e.message); }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [active, revision]);
  return { reviews, error, loading };
}
export function OwnerReviewLink({ subjectId, subjectKind, lookup }: {
  subjectId: string; subjectKind: string; lookup: ReturnType<typeof useOwnerReviewLinks>;
}) {
  const matches = lookup.reviews.filter(review => review.subject_id === subjectId && review.subject_kind === subjectKind);
  const review = matches.find(item => item.decision === 'Pending') || matches[0];
  if (lookup.loading) return <p role="status" className="muted">Finding the linked review…</p>;
  if (lookup.error) return <p role="alert" className="error">The linked review could not be loaded. Refresh this page to try again.</p>;
  if (!review) return <p className="muted">No review is available for this exact record. Refresh to check for a new submission.</p>;
  return <a className="text-button" href={routeHash({ page: 'approvals', id: review.id }, readRoute().conversation)}>Open this review</a>;
}

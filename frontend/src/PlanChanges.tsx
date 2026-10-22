type SavedPlan = {
  id: string;
  kind: string;
  version: number;
  baseline_id?: string | null;
  content: Record<string, unknown>;
};

const missing = Symbol('missing');
type Change = { path: string[]; before: unknown; after: unknown };

function changes(before: unknown, after: unknown, path: string[] = []): Change[] {
  if (Object.is(before, after)) return [];
  if (before !== null && after !== null && typeof before === 'object' && typeof after === 'object'
      && Array.isArray(before) === Array.isArray(after)) {
    const left = before as Record<string, unknown>, right = after as Record<string, unknown>;
    return [...new Set([...Object.keys(left), ...Object.keys(right)])].flatMap(key =>
      changes(Object.hasOwn(left, key) ? left[key] : missing, Object.hasOwn(right, key) ? right[key] : missing, [...path, key]));
  }
  return [{ path, before, after }];
}

const fields: Record<string, string> = {
  sessions: 'Sessions', exercises: 'Exercises', meals: 'Meals', portions: 'Portions',
  exercise_id: 'Exercise', food_id: 'Food', grams: 'Quantity (g)',
  training_version_id: 'Associated training plan', nutrient_totals: 'Nutrient totals',
  seconds_per_set: 'Seconds per set', rest_seconds: 'Rest (seconds)',
  warmup_minutes: 'Warm-up (minutes)', cooldown_minutes: 'Cool-down (minutes)',
  transition_minutes: 'Transitions (minutes)',
};
const items: Record<string, string> = { sessions: 'Session', exercises: 'Exercise', meals: 'Meal', portions: 'Portion', limitations: 'Limitation' };
function fieldName(path: string[]): string {
  const names: string[] = [];
  for (let i = 0; i < path.length; i++) {
    const key = path[i];
    if (items[key] && /^\d+$/.test(path[i + 1] || '')) {
      names.push(`${items[key]} ${Number(path[++i]) + 1}`);
    } else names.push(fields[key] || key.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase()));
  }
  return names.join(' / ') || 'Plan';
}
function Value({ value, labels }: { value: unknown; labels: Record<string, string> }) {
  if (value === missing) return <span>Not present</span>;
  if (value === null) return <span>Not specified</span>;
  if (typeof value === 'boolean') return <span>{value ? 'Yes' : 'No'}</span>;
  if (Array.isArray(value)) return value.length ? <ul>{value.map((item, i) => <li key={i}><Value value={item} labels={labels}/></li>)}</ul> : <span>None</span>;
  if (typeof value === 'object' && value) return <dl>{Object.entries(value).map(([key, item]) => <div key={key}><dt>{fieldName([key])}</dt><dd><Value value={item} labels={labels}/></dd></div>)}</dl>;
  return <span>{labels[String(value)] || String(value)}</span>;
}

export function PlanChanges({ plan, plans, labels }: { plan: SavedPlan; plans: SavedPlan[]; labels: Record<string, string> }) {
  if (!plan.baseline_id) return null;
  const baseline = plans.find(row => row.id === plan.baseline_id && row.kind === plan.kind);
  if (!baseline) return <p className="muted">The earlier version is unavailable for comparison.</p>;
  const rows = changes(baseline.content, plan.content);
  const displayLabels = { ...labels, ...Object.fromEntries(plans.map(row => [row.id, `${row.kind} version ${row.version}`])) };
  return <details className="plan-changes">
    <summary>Changes from {baseline.kind} version {baseline.version}</summary>
    <p className="muted">Compared with the saved version used to prepare this revision. Reading this comparison does not change your active plan.</p>
    {rows.length ? <div className="table-wrap"><table>
      <thead><tr><th>Changed detail</th><th>Previous version</th><th>This version</th></tr></thead>
      <tbody>{rows.map(row => <tr key={JSON.stringify(row.path)}><th scope="row">{fieldName(row.path)}</th><td><Value value={row.before} labels={displayLabels}/></td><td><Value value={row.after} labels={displayLabels}/></td></tr>)}</tbody>
    </table></div> : <p>No plan content changed from this earlier version.</p>}
  </details>;
}

export type Page = 'chat' | 'intake' | 'approvals' | 'plans' | 'dashboard' | 'notifications' | 'groups' | 'incidents' | 'budgets' | 'members' | 'versions' | 'releases' | 'requests';
export type OwnerTarget = { page: Page; id?: string; section?: string };
export type WorkspaceRoute = OwnerTarget & { conversation?: string };
const pages: Page[] = ['chat', 'intake', 'approvals', 'plans', 'dashboard', 'notifications', 'groups', 'incidents', 'budgets', 'members', 'versions', 'releases', 'requests'];
export const ownerPages: Page[] = ['approvals', 'dashboard', 'groups', 'incidents', 'budgets', 'members', 'versions', 'releases'];
export function readRoute(hash = window.location.hash): WorkspaceRoute {
  try {
    const [path, search = ''] = hash.slice(1).split('?');
    const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
    const params = new URLSearchParams(search);
    if (parts[0] === 'assistant') return { page: 'chat', conversation: parts[1] };
    const page = parts[1] as Page;
    if (['owner', 'workspace'].includes(parts[0]) && pages.includes(page)) {
      return { page, id: parts[2], section: params.get('section') || undefined, conversation: params.get('conversation') || undefined };
    }
  } catch { /* An invalid URL never becomes an action target. */ }
  return { page: 'chat' };
}
export function routeHash(target: OwnerTarget, conversation?: string): string {
  if (target.page === 'chat') return `#/assistant${conversation ? `/${encodeURIComponent(conversation)}` : ''}`;
  const params = new URLSearchParams();
  if (conversation) params.set('conversation', conversation);
  if (target.section) params.set('section', target.section);
  const path = `#/${ownerPages.includes(target.page) ? 'owner' : 'workspace'}/${target.page}${target.id ? `/${encodeURIComponent(target.id)}` : ''}`;
  return path + (params.size ? `?${params}` : '');
}
export function notifyOwnerChange() { window.dispatchEvent(new Event('owner-workspace-changed')); }

export function openOwnerRecord(target:OwnerTarget, conversation?:string) {
 if(document.querySelector("[data-owner-panel]"))window.dispatchEvent(new CustomEvent("owner-panel-navigate",{detail:target}));
 else window.location.hash=routeHash(target,conversation);
}

let csrf = '';
export function setCsrf(value: string) { csrf = value; }
export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, { credentials: 'same-origin', method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : {'Content-Type':'application/json','x-csrf-token':csrf},
    body: body === undefined ? undefined : JSON.stringify(body) });
  const value = await response.json();
  if (!response.ok) {
    const messages = Array.isArray(value.detail) ? value.detail
      .filter((item: {msg?:unknown}) => typeof item.msg === 'string')
      .map((item: {msg:string}) => item.msg.replace(/^Value error, /, '')) : [];
    throw new Error(typeof value.detail === 'string' ? value.detail : messages.length ? messages.join(' ') : 'Please check the information and try again.');
  }
  return value as T;
}

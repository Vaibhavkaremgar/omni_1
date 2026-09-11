import { getToken } from '../auth/localAuth';
import { backendUrl } from './url';

export async function backendFetch(path: string, init: RequestInit = {}) {
  const token = getToken();
  const headers = new Headers(init.headers);
  // Only set Content-Type for non-FormData bodies
  if (!(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return fetch(backendUrl(path), { ...init, headers });
}

export async function backendJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await backendFetch(path, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const error = new Error(
      typeof detail === 'string' ? detail : detail?.message ?? 'Backend request failed',
    ) as Error & { status?: number; payload?: unknown };
    error.status = response.status;
    error.payload = detail;
    throw error;
  }
  return body as T;
}

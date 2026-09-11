const TOKEN_STORAGE_KEY = 'pontis_access_token';
import { backendUrl } from '../backend/url';

export interface LocalUser {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
  status: string;
}

interface AuthResponse {
  access_token: string;
  user: LocalUser;
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
}

export async function authenticate(path: 'login' | 'register', email: string, password: string): Promise<LocalUser> {
  const response = await fetch(backendUrl(`/auth/${path}`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.detail ?? 'Authentication failed');
  const result = body as AuthResponse;
  localStorage.setItem(TOKEN_STORAGE_KEY, result.access_token);
  return result.user;
}

export async function getCurrentUser(): Promise<LocalUser | null> {
  const token = getToken();
  if (!token) return null;
  const response = await fetch(backendUrl('/auth/me'), {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    clearToken();
    return null;
  }
  const body = await response.json();
  return body.user as LocalUser;
}

export async function logout(): Promise<void> {
  const token = getToken();
  try {
    if (token) await fetch(backendUrl('/auth/logout'), { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
  } finally {
    clearToken();
  }
}

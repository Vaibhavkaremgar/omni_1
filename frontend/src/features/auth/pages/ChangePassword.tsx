import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { backendJson } from '../../../services/backend/api';
import { useAuth } from '../hooks/useAuth';

interface ChangedAuth { access_token: string; user: Parameters<ReturnType<typeof useAuth>['signIn']>[0] }

export default function ChangePassword() {
  const [password, setPassword] = useState(''); const [error, setError] = useState('');
  const navigate = useNavigate(); const { signIn } = useAuth();
  async function submit(e: React.FormEvent) { e.preventDefault(); setError(''); try { const result = await backendJson<ChangedAuth>('/auth/change-password', { method: 'POST', body: JSON.stringify({ new_password: password }) }); localStorage.setItem('pontis_access_token', result.access_token); signIn(result.user); navigate(result.user.role === 'admin' ? '/admin' : '/dashboard', { replace: true }); } catch (err) { setError(err instanceof Error ? err.message : 'Unable to change password'); } }
  return <main className="min-h-screen flex items-center justify-center bg-gray-50"><form onSubmit={submit} className="bg-white p-8 rounded-xl shadow-sm w-full max-w-sm"><h1 className="text-2xl font-bold">Set a new password</h1><p className="text-gray-500 mt-2">Choose a new password before continuing.</p><input className="w-full mt-6 border rounded-lg p-3" type="password" minLength={8} required value={password} onChange={e => setPassword(e.target.value)} placeholder="New password" />{error && <p className="text-red-600 text-sm mt-3">{error}</p>}<button className="w-full mt-4 bg-blue-600 text-white rounded-lg p-3">Continue</button></form></main>;
}

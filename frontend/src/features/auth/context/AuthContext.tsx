import { useEffect, useState, ReactNode } from 'react';
import { getCurrentUser, LocalUser, logout } from '../../../services/auth/localAuth';
import { AuthContext } from './AuthContextValue';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<LocalUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCurrentUser().then(setUser).finally(() => setLoading(false));
  }, []);

  const signOut = async () => {
    await logout();
    setUser(null);
  };

  useEffect(() => {
    const handleUnauthorized = () => { setUser(null); setLoading(false); };
    window.addEventListener('pontis:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('pontis:unauthorized', handleUnauthorized);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, signIn: setUser, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

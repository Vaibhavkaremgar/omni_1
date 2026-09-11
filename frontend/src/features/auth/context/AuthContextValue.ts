import { createContext } from 'react';
import { LocalUser } from '../../../services/auth/localAuth';

export interface AuthContextType {
  user: LocalUser | null;
  loading: boolean;
  signIn: (user: LocalUser) => void;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  signIn: () => {},
  signOut: async () => {},
});

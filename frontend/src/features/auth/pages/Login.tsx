import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { authenticate, getToken } from '../../../services/auth/localAuth';
import { useAuth } from '../hooks/useAuth';

interface LocationState {
  from?: { pathname: string };
}

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as LocationState)?.from?.pathname || '/dashboard';
  const { signIn } = useAuth();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (getToken()) navigate(from, { replace: true });
  }, [from, navigate]);

  const handleAuth = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const user = await authenticate('login', email, password);
      signIn(user);
      navigate(user.must_change_password ? '/change-password' : user.role === 'admin' ? '/admin' : from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 flex">
      {/* Left panel */}
      <div className="hidden lg:flex lg:w-1/2 bg-white flex-col justify-between p-10">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-blue-600 flex items-center justify-center text-white font-bold">PC</div>
          <span className="text-xl font-bold text-gray-900">Pontis Calls</span>
        </div>

        <div className="mt-12">
          <h1 className="text-4xl font-bold text-gray-900 leading-tight">
            Your AI employees<br />make the calls.
          </h1>
          <p className="mt-4 text-lg text-gray-600 max-w-md">
            Create intelligent voice agents in minutes. Call leads instantly, run bulk campaigns, and review every conversation — all from one place.
          </p>
        </div>

        <div className="mt-16 grid grid-cols-3 gap-6 text-center">
          <div>
            <div className="text-3xl font-bold text-blue-600">12,400+</div>
            <div className="text-sm text-gray-500 mt-1">AI calls made today</div>
          </div>
          <div>
            <div className="text-3xl font-bold text-blue-600">94%</div>
            <div className="text-sm text-gray-500 mt-1">Connection rate</div>
          </div>
          <div>
            <div className="text-3xl font-bold text-blue-600">2.3 min</div>
            <div className="text-sm text-gray-500 mt-1">Average call duration</div>
          </div>
        </div>

        <p className="text-sm text-gray-400">Trusted by 800+ sales and support teams</p>
      </div>

      {/* Right panel */}
      <div className="flex-1 flex items-center justify-center p-6 bg-gray-50">
        <div className="w-full max-w-sm">
          <div className="lg:hidden flex items-center gap-3 mb-8">
            <div className="w-9 h-9 rounded-lg bg-blue-600 flex items-center justify-center text-white font-bold">PC</div>
            <span className="text-lg font-bold text-gray-900">Pontis Calls</span>
          </div>

          <h2 className="text-2xl font-bold text-gray-900">
            Welcome back
          </h2>
          <p className="mt-2 text-gray-500">
            Sign in to your Pontis account.
          </p>

          <form onSubmit={handleAuth} className="mt-6 space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Email address</label>
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="you@company.com"
                className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 transition"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="Enter your password"
                className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 transition"
                required
                minLength={6}
              />
            </div>

            {error && (
              <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-sm text-rose-700">{error}</div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white font-medium rounded-lg transition-colors text-sm flex items-center justify-center gap-2"
            >
              {loading ? (
                <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              ) : 'Sign in'}
            </button>
          </form>

        </div>
      </div>
    </div>
  );
}

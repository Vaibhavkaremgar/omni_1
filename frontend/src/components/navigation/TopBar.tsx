import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../../features/auth/hooks/useAuth';

export default function TopBar() {
  const { signOut } = useAuth();
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const searchResults = (() => {
    if (!searchQuery.trim()) return [];
    const q = searchQuery.toLowerCase();
    const results: Array<{ label: string; sub: string; action?: string }> = [];

    if (q.includes('ava') || q.includes('lead')) results.push({ label: 'Ava — Lead Qualifier', sub: 'AI Employee', action: '/employees' });
    if (q.includes('marcus') || q.includes('outreach')) results.push({ label: 'Marcus — Outreach Rep', sub: 'AI Employee', action: '/employees' });
    if (q.includes('instant') || q.includes('call now')) results.push({ label: 'Instant Leads', sub: 'Call someone right now', action: '/instant' });
    if (q.includes('campaign') || q.includes('bulk')) results.push({ label: 'Bulk Campaigns', sub: 'Run outbound campaigns', action: '/campaigns' });
    if (q.includes('billing') || q.includes('credit')) results.push({ label: 'Billing', sub: 'Usage & credits', action: '/billing' });
    if (q.includes('number') || q.includes('phone')) results.push({ label: 'Phone Numbers', sub: 'Manage your numbers', action: '/numbers' });
    if (q.includes('settings') || q.includes('profile')) results.push({ label: 'Settings', sub: 'Account & preferences', action: '/settings' });
    if (q.includes('employee') || q.includes('create ai')) results.push({ label: 'Create AI Employee', sub: 'New employee wizard', action: '/employees/new' });

    const seen = new Set<string>();
    return results.filter(r => {
      if (seen.has(r.label)) return false;
      seen.add(r.label);
      return true;
    });
  })();

  return (
    <header className="h-16 bg-white border-b border-gray-200 flex items-center px-4 lg:px-6 gap-4 sticky top-0 z-30">
      <button className="lg:hidden p-2 -ml-2 rounded-md hover:bg-gray-100 text-gray-500">
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>

      <div className="flex-1 max-w-xl relative">
        <div className="relative">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            placeholder="Search employees, calls, numbers, campaigns..."
            value={searchQuery}
            onChange={e => { setSearchQuery(e.target.value); setSearchOpen(true); }}
            onFocus={() => setSearchOpen(true)}
            onBlur={() => setTimeout(() => setSearchOpen(false), 150)}
            className="w-full pl-9 pr-4 py-2 text-sm bg-gray-50 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 transition"
          />
        </div>

        {searchOpen && (searchResults.length > 0 || searchQuery.trim()) && (
          <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg max-h-64 overflow-y-auto z-50">
            {searchResults.map((result, i) => (
              <Link
                key={i}
                to={result.action || '#'}
                className="flex items-center justify-between px-4 py-2.5 hover:bg-gray-50 border-b border-gray-100 last:border-0 cursor-pointer"
                onClick={() => { setSearchOpen(false); setSearchQuery(''); }}
              >
                <div>
                  <div className="text-sm font-medium text-gray-900">{result.label}</div>
                  <div className="text-xs text-gray-500">{result.sub}</div>
                </div>
                <svg className="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </Link>
            ))}
            {searchResults.length === 0 && searchQuery.trim() && (
              <div className="px-4 py-6 text-center text-sm text-gray-500">
                No results for "{searchQuery}"
              </div>
            )}
          </div>
        )}
      </div>

      <div className="flex items-center gap-3">
        <button className="relative p-2 rounded-md hover:bg-gray-100 text-gray-500 transition-colors">
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          </svg>
          <span className="absolute top-1 right-1 w-2 h-2 bg-rose-500 rounded-full" />
        </button>

        <div className="flex items-center gap-2 pl-2 border-l border-gray-200">
          <div className="w-7 h-7 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 text-xs font-semibold">
            JD
          </div>
          <div className="hidden sm:block text-sm leading-tight">
            <div className="font-medium text-gray-900">Jacob Dale</div>
          </div>
          <button
            onClick={signOut}
            className="p-1.5 rounded-md hover:bg-gray-100 text-gray-400 hover:text-rose-600 transition-colors"
            title="Sign out"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3h-4a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
            </svg>
          </button>
        </div>
      </div>
    </header>
  );
}

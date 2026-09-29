import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../../features/auth/hooks/useAuth';
import { backendJson } from '../../services/backend/api';

type Branding = { business_name: string; logo_data_url: string | null };

function businessInitials(name: string) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return 'PC';
  return (words.length === 1 ? words[0].slice(0, 2) : `${words[0][0]}${words[1][0]}`).toUpperCase();
}

const NAV_ITEMS = [
  { path: '/dashboard', label: 'Dashboard', icon: '📊' },
  { path: '/employees', label: 'My Employees', icon: '👥' },
  { path: '/instant', label: 'Instant Leads', icon: '⚡' },
  { path: '/campaigns', label: 'Bulk Campaigns', icon: '📢' },
  { path: '/calls', label: 'Calls', icon: '📞' },
  { path: '/numbers', label: 'Phone Numbers', icon: '☎️' },
  { path: '/integrations', label: 'Integrations', icon: '🔌' },
  { path: '/billing', label: 'Billing', icon: '💳' },
  { path: '/settings', label: 'Settings', icon: '⚙️' },
];

interface SidebarProps {
  collapsed: boolean;
  mobileOpen?: boolean;
  onToggle: () => void;
}

export default function Sidebar({ collapsed, mobileOpen = false, onToggle }: SidebarProps) {
  const location = useLocation();
  const { user } = useAuth();
  const [branding, setBranding] = useState<Branding>({ business_name: 'Pontis Calls', logo_data_url: null });

  useEffect(() => {
    void backendJson<Branding>('/settings').then(setBranding).catch(() => undefined);
    const updateBranding = (event: Event) => setBranding((event as CustomEvent<Branding>).detail);
    window.addEventListener('pontis:branding-updated', updateBranding);
    return () => window.removeEventListener('pontis:branding-updated', updateBranding);
  }, []);

  const brandMark = branding.logo_data_url
    ? <img src={branding.logo_data_url} alt="" className="h-7 w-7 rounded-md object-cover" />
    : <span className="flex h-7 w-7 items-center justify-center rounded-md bg-blue-600 text-xs font-bold text-white">{businessInitials(branding.business_name)}</span>;

  return (
    <aside
      className={`${mobileOpen ? 'fixed inset-y-0 left-0 z-40' : 'hidden lg:flex'} h-full shrink-0 flex-col bg-white border-r border-gray-200 transition-all duration-300 ${
        collapsed ? 'w-[68px]' : 'w-60'
      }`}
    >
      {/* Logo */}
      <div className="flex items-center justify-between h-16 px-4 border-b border-gray-200">
        {!collapsed && (
          <Link to="/dashboard" className="flex items-center gap-2 font-semibold text-gray-900">
            {brandMark}
            <span className="max-w-32 truncate">{branding.business_name}</span>
          </Link>
        )}
        {collapsed && (
          <Link to="/dashboard" className="flex items-center justify-center w-full">
            {brandMark}
          </Link>
        )}
        <button
          onClick={onToggle}
          className="p-1.5 rounded-md hover:bg-gray-100 text-gray-500 transition-colors ml-auto"
          title="Toggle sidebar"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            {collapsed ? (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
            ) : (
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            )}
          </svg>
        </button>
      </div>

      {/* Nav items */}
      <nav className="flex-1 px-2 py-3 space-y-1 overflow-y-auto">
        {NAV_ITEMS.map(item => {
          const isActive = location.pathname === item.path ||
            (item.path !== '/dashboard' && location.pathname.startsWith(item.path));
          return (
            <Link
              key={item.path}
              to={item.path}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                isActive
                  ? 'bg-blue-50 text-blue-700'
                  : 'text-gray-700 hover:bg-gray-100 hover:text-gray-900'
              }`}
              title={collapsed ? item.label : undefined}
            >
              {!collapsed && <span className="text-base">{item.icon}</span>}
              <span className={!collapsed ? 'flex-1' : 'sr-only'}>{item.label}</span>
              {collapsed && (
                <span className="absolute left-12 top-2 text-xs text-gray-600 bg-white px-2 py-0.5 rounded border border-gray-200 whitespace-nowrap opacity-0 group-hover:opacity-100"
                  style={{ transition: 'opacity 0.15s' }}
                  onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
                  onMouseLeave={e => (e.currentTarget.style.opacity = '0')}
                >
                  {item.label}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* User pill at bottom */}
      <div className="px-3 py-3 border-t border-gray-200">
        <div className={`flex items-center gap-3 ${collapsed ? 'justify-center' : ''}`}>
          <div className="w-7 h-7 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 text-xs font-semibold flex-shrink-0">
              {(user?.full_name || user?.email || 'U').slice(0, 2).toUpperCase()}
          </div>
          {!collapsed && (
            <div className="min-w-0 flex-1 truncate text-sm font-medium text-gray-700">{user?.email}</div>
          )}
        </div>
      </div>
    </aside>
  );
}

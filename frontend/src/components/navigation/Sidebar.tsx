import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../../features/auth/hooks/useAuth';

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
  onToggle: () => void;
}

export default function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const location = useLocation();
  const { user } = useAuth();

  return (
    <aside
      className={`h-screen sticky top-0 flex flex-col bg-white border-r border-gray-200 transition-all duration-300 ${
        collapsed ? 'w-[68px]' : 'w-60'
      }`}
    >
      {/* Logo */}
      <div className="flex items-center justify-between h-16 px-4 border-b border-gray-200">
        {!collapsed && (
          <Link to="/dashboard" className="flex items-center gap-2 font-semibold text-gray-900">
            <span className="w-7 h-7 rounded-md bg-blue-600 flex items-center justify-center text-white text-sm font-bold">
              PC
            </span>
            <span>Pontis Calls</span>
          </Link>
        )}
        {collapsed && (
          <Link to="/dashboard" className="flex items-center justify-center w-full">
            <span className="w-7 h-7 rounded-md bg-blue-600 flex items-center justify-center text-white text-sm font-bold">
              PC
            </span>
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
            <div className="text-sm leading-tight">
              <div className="font-medium text-gray-900 truncate">{user?.full_name || user?.email}</div>
              <div className="text-xs text-gray-500 truncate">{user?.email}</div>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}

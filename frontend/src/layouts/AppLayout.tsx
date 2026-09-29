import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from '../components/navigation/Sidebar';
import TopBar from '../components/navigation/TopBar';

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  useEffect(() => { const handler = () => setMobileOpen(value => !value); window.addEventListener('pontis:toggle-mobile-nav', handler); return () => window.removeEventListener('pontis:toggle-mobile-nav', handler); }, []);

  return (
    <div className="fixed inset-0 flex overflow-hidden bg-gray-50">
      <Sidebar collapsed={collapsed} mobileOpen={mobileOpen} onToggle={() => setCollapsed(c => !c)} />
      <div className="flex h-full flex-1 min-w-0 min-h-0 flex-col overflow-hidden">
        <TopBar />
        <main className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

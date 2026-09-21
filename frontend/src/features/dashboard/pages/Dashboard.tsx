import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { backendJson } from '../../../services/backend/api';
import { useAuth } from '../../auth/hooks/useAuth';
import {
  PhoneCall,
  Users,
  Megaphone,
  CreditCard,
  Settings,
  Plus,
  Clock,
  CheckCircle,
  AlertCircle,
  TrendingUp,
  PhoneIncoming,
  ArrowUpRight,
  User,
  Gift,
} from 'lucide-react';

interface Employee {
  id: string;
  name: string;
  description?: string | null;
  purpose?: string;
  voice: string;
  language: string;
  status: string;
  inbound_enabled: boolean;
  outbound_enabled: boolean;
  phone_number_id: string | null;
  calls_today: number;
  is_active: boolean;
  last_updated: string;
  prompt?: string;
}

interface PhoneNumber {
  id: string;
  number: string;
  country: string;
  provider: string;
  status: string;
  assigned_employee_id: string | null;
}

interface Call {
  id: string;
  phone: string;
  status: string;
  duration: number | null;
  is_inbound: boolean;
  is_answered: boolean;
  started_at: string;
  ended_at: string | null;
  employee_id: string | null;
  lead_status: string | null;
  context: string | null;
}

interface Campaign {
  id: string;
  name: string;
  status: string;
  total_contacts: number;
  completed: number;
  connected: number;
  no_answer: number;
  failed: number;
  minutes_used: string;
}

interface Notification {
  id: string;
  type: string;
  title: string;
  message: string;
  time: string;
  read: boolean;
}
interface CouponOffer { id: string; title: string; description?: string; promotional_minutes?: number; expires_at?: string; status: string }
interface DashboardSummary {
  calls_today: number; connected_today: number; credits: number;
  recent_calls: Array<{ id: string; customer_phone_number: string | null; status: string; duration_seconds: number | null; direction: string; started_at: string; employee_id: string | null; outcome: string | null }>;
  employees: Array<{ id: string; name: string; purpose: string; language: string; status: string; is_ready: boolean }>;
  campaigns: Array<{ id: string; name: string; status: string; scheduled_at: string | null; completed: number; total: number }>;
  alerts: Array<{ type: string; title: string; message: string; severity: string }>;
}

export default function Dashboard() {
  const { user } = useAuth();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [recentCalls, setRecentCalls] = useState<Call[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [offers, setOffers] = useState<CouponOffer[]>([]);
  const [credits, setCredits] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [empData, numData, callsData, billingData, walletData] = await Promise.all([
          backendJson<Employee[]>('/employees'),
          backendJson<PhoneNumber[]>('/phone-numbers'),
          backendJson<Call[]>('/calls'),
          backendJson<Array<{ id: string; transaction_type: string; amount: number; description: string; created_at: string }>>('/billing/transactions'),
          backendJson<{ balance: number }>('/billing/wallet'),
        ]);

        setEmployees(empData || []);
        setNumbers(numData || []);
        setRecentCalls((callsData || []).slice(0, 8));
        setCredits(Number(walletData?.balance || 0));
        setCampaigns([]);

        // Build notifications from billing data
        const bills = billingData || [];
        const notifs: Notification[] = [];
        bills.slice(0, 4).forEach(b => {
          if (b.transaction_type === 'debit') {
            notifs.push({
              id: b.id,
              type: 'usage',
              title: 'Credits used',
              message: `${Math.abs(b.amount)} minutes consumed — ${b.description}`,
              time: new Date(b.created_at).toISOString(),
              read: false,
            });
          } else {
            notifs.push({
              id: b.id,
              type: 'credit',
              title: 'Credits added',
              message: `${b.amount} minutes added — ${b.description}`,
              time: new Date(b.created_at).toISOString(),
              read: true,
            });
          }
        });
        setNotifications(notifs);
      } catch (err) {
        console.error('Dashboard fetch error:', err);
        setError('Failed to load dashboard data.');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);
  useEffect(() => { void backendJson<CouponOffer[]>('/coupons/offers').then(setOffers).catch(() => setOffers([])); }, []);

  useEffect(() => {
    let cancelled = false;
    backendJson<DashboardSummary>('/dashboard/summary').then(summary => {
      if (cancelled) return;
      setCredits(summary.credits || 0);
      setCampaigns((summary.campaigns || []).map(c => ({ ...c, total_contacts: c.total, connected: 0, no_answer: 0, failed: 0, minutes_used: '0' })) as Campaign[]);
      setNotifications((summary.alerts || []).map((a, index) => ({ id: `${a.type}-${index}`, type: a.severity, title: a.title, message: a.message, time: new Date().toISOString(), read: false })));
      setRecentCalls((summary.recent_calls || []).map(c => ({ id: c.id, phone: c.customer_phone_number || '—', status: c.status, duration: c.duration_seconds, is_inbound: c.direction === 'inbound', is_answered: c.status === 'completed', started_at: c.started_at, ended_at: null, employee_id: c.employee_id, lead_status: null, context: c.outcome })));
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  // Compute metrics
  const totalCalls = recentCalls.length;
  const inboundCalls = recentCalls.filter(c => c.is_inbound && c.is_answered).length;
  const outboundCalls = recentCalls.filter(c => !c.is_inbound).length;
  const connectedCalls = recentCalls.filter(c => c.is_answered).length;
  const totalMinutes = recentCalls.reduce((sum, c) => sum + (c.duration || 0), 0);
  const totalCredits = credits;
  const leadsConversions = recentCalls.filter(c => c.lead_status === 'converted').length;

  const formatDuration = (seconds: number) => {
    if (!seconds) return '—';
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  };

  const formatTime = (iso: string) => {
    const d = new Date(iso);
    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();
    return isToday
      ? d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
      : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  };

  if (error && !loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center max-w-md">
          <div className="w-12 h-12 bg-rose-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <AlertCircle className="w-6 h-6 text-rose-600" />
          </div>
          <h2 className="text-lg font-bold text-gray-900 mb-1">Something went wrong</h2>
          <p className="text-sm text-gray-600 mb-4">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg text-sm transition"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex-1 flex flex-col">
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          <div className="h-8 w-48 bg-gray-200 rounded animate-pulse" />
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[...Array(8)].map((_, i) => (
              <div key={i} className="h-24 bg-gray-100 rounded-xl animate-pulse" />
            ))}
          </div>
          <div className="h-64 bg-gray-100 rounded-xl animate-pulse" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="h-16 flex items-center justify-between px-6 border-b border-gray-200 bg-white">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-sm text-gray-500">
            Welcome, {user?.full_name || user?.email || 'there'}. Here's what's happening today.
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Clock className="w-4 h-4" />
          <span>
            {new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {offers.length > 0 && <section className="rounded-xl border border-gray-200 bg-white p-3 shadow-sm"><div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div className="flex min-w-0 items-start gap-3"><div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600" aria-label="Special offer"><Gift className="h-4 w-4" aria-hidden="true" /></div><div className="min-w-0"><p className="text-xs font-semibold uppercase tracking-wide text-blue-600">Special offer</p><h2 className="mt-0.5 break-words text-sm font-semibold text-gray-900">{offers[0].title}</h2><p className="mt-0.5 break-words text-sm text-gray-600">{offers[0].description || `${offers[0].promotional_minutes} free calling minutes`}</p>{offers[0].expires_at && <p className="mt-1 text-xs text-gray-500">Valid until {new Date(offers[0].expires_at).toLocaleDateString()}</p>}{offers.length > 1 && <p className="mt-1 text-xs text-blue-600">+{offers.length - 1} more offer{offers.length > 2 ? 's' : ''}</p>}</div></div><button onClick={() => void backendJson(`/coupons/offers/${offers[0].id}/view`, { method: 'POST' })} className="w-full shrink-0 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 sm:w-auto">View offer</button></div></section>}
        {/* Quick Actions */}
        <section className="mb-2">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Quick Actions</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: 'Create AI Employee', desc: 'New voice agent', icon: Plus, href: '/employees/new', color: 'bg-blue-600 hover:bg-blue-700' },
              { label: 'Make Instant Call', desc: 'Call a lead now', icon: PhoneCall, href: '/instant', color: 'bg-emerald-600 hover:bg-emerald-700' },
              { label: 'Bulk Campaign', desc: 'Outreach to many', icon: Megaphone, href: '/campaigns', color: 'bg-violet-600 hover:bg-violet-700' },
              { label: 'Buy Credits', desc: '+ calling minutes', icon: CreditCard, href: '/billing', color: 'bg-amber-600 hover:bg-amber-700' },
            ].map(action => (
              <Link
                key={action.href}
                to={action.href}
                className={`${action.color} text-white rounded-xl p-4 text-left transition-all hover:shadow-lg hover:-translate-y-0.5 group`}
              >
                <div className="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center mb-3 group-hover:bg-white/30 transition">
                  <action.icon className="w-4 h-4" />
                </div>
                <div className="font-medium text-sm">{action.label}</div>
                <div className="text-xs text-white/80 mt-0.5">{action.desc}</div>
              </Link>
            ))}
          </div>
        </section>

        {/* At a Glance Metrics */}
        <section>
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">At a Glance</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { label: 'Calls today', value: totalCalls, sub: `${inboundCalls} inbound · ${outboundCalls} outbound`, icon: PhoneCall, color: 'text-blue-600 bg-blue-50' },
              { label: 'Connected', value: connectedCalls, sub: `${(totalCalls ? (connectedCalls/totalCalls*100).toFixed(0) : 0)}% connection rate`, icon: CheckCircle, color: 'text-emerald-600 bg-emerald-50' },
              { label: 'Leads & conversions', value: `${leadsConversions} conv.`, sub: `${totalCalls} calls made`, icon: TrendingUp, color: 'text-violet-600 bg-violet-50' },
              { label: 'Credits remaining', value: `${totalCredits} min`, sub: `${(totalMinutes/60).toFixed(1)} hours used`, icon: CreditCard, color: 'text-amber-600 bg-amber-50' },
            ].map(metric => (
              <div key={metric.label} className="bg-white border border-gray-200 rounded-xl p-4 flex items-start gap-3 hover:shadow-sm transition-shadow">
                <div className={`w-9 h-9 ${metric.color} rounded-lg flex items-center justify-center flex-shrink-0`}>
                  <metric.icon className="w-4.5 h-4.5" />
                </div>
                <div>
                  <div className="text-xl font-bold text-gray-900">{metric.value}</div>
                  <div className="text-xs text-gray-500 mt-0.5">{metric.sub}</div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Two-column: Notifications + Employees snapshot */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Needs Your Attention */}
          <section className="lg:col-span-1">
            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <AlertCircle className="w-4 h-4 text-amber-500" />
                  <h2 className="text-sm font-semibold text-gray-900">Needs Your Attention</h2>
                </div>
                <span className="text-xs text-gray-500">{notifications.filter(n => !n.read).length} unread</span>
              </div>
              <div className="divide-y divide-gray-100 max-h-64 overflow-y-auto">
                {notifications.length === 0 ? (
                  <div className="px-4 py-8 text-center text-sm text-gray-500">
                    Everything looks good.
                  </div>
                ) : (
                  notifications.map(n => (
                    <div
                      key={n.id}
                      className={`px-4 py-3 flex items-start gap-3 ${n.read ? 'opacity-60' : 'bg-blue-50/30'}`}
                    >
                      <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${
                        n.type === 'attention' ? 'bg-amber-500' : n.type === 'usage' ? 'bg-rose-500' : 'bg-blue-400'
                      }`} />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-gray-900 truncate">{n.title}</div>
                        <div className="text-xs text-gray-500 mt-0.5 line-clamp-2">{n.message}</div>
                        <div className="text-xs text-gray-400 mt-1">
                          {formatTime(n.time)}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </section>

          {/* Your AI Employees */}
          <section className="lg:col-span-2">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">Your AI Employees</h2>
              <Link
                to="/employees"
                className="text-sm text-blue-600 hover:text-blue-700 font-medium flex items-center gap-1"
              >
                View all
                <ArrowUpRight className="w-3.5 h-3.5" />
              </Link>
            </div>
            {employees.length === 0 ? (
              <div className="bg-white border border-gray-200 rounded-xl p-8 text-center">
                <div className="w-10 h-10 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-3">
                  <Users className="w-5 h-5 text-gray-400" />
                </div>
                <h3 className="text-sm font-medium text-gray-900 mb-1">No employees yet</h3>
                <p className="text-xs text-gray-500 mb-4">Create your first AI employee to start making calls.</p>
                <Link
                  to="/employees/new"
                  className="text-blue-600 hover:text-blue-700 text-sm font-medium"
                >
                  Create employee
                </Link>
              </div>
            ) : (
              <div className="space-y-2">
                {employees.map(emp => {
                  const phone = numbers.find(n => n.id === emp.phone_number_id);
                  return (
                    <div
                      key={emp.id}
                      className="bg-white border border-gray-200 rounded-xl p-4 flex items-center gap-4 hover:shadow-sm transition-shadow group"
                    >
                      {/* Avatar */}
                      <div className="w-10 h-10 rounded-full bg-blue-100 flex items-center justify-center flex-shrink-0">
                        <User className="w-5 h-5 text-blue-600" />
                      </div>

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-gray-900 truncate">{emp.name}</span>
                          <span className={`text-xs px-1.5 py-0.5 rounded-full flex-shrink-0 ${
                            emp.is_active ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-500'
                          }`}>
                            {emp.is_active ? 'Active' : 'Inactive'}
                          </span>
                          {emp.status === 'draft' && (
                            <span className="text-xs px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 flex-shrink-0">Draft</span>
                          )}
                        </div>
                        <div className="text-xs text-gray-500 mt-0.5 truncate">
                          {emp.purpose || emp.description || `${emp.voice} · ${emp.language}`}
                        </div>
                        <div className="flex items-center gap-3 mt-1 text-xs text-gray-500">
                          <span>{emp.voice}</span>
                          {phone && <span>{phone.number}</span>}
                          <span className="flex items-center gap-1">
                            <PhoneIncoming className="w-3 h-3" />
                            {emp.calls_today} today
                          </span>
                        </div>
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <Link
                          to={`/employees/${emp.id}`}
                          className="p-1.5 rounded-md hover:bg-gray-100 text-gray-500"
                          title="View details"
                        >
                          <Settings className="w-4 h-4" />
                        </Link>
                        <Link
                          to={`/instant?employee=${emp.id}`}
                          className="p-1.5 rounded-md hover:bg-gray-100 text-gray-500"
                          title="Call with this employee"
                        >
                          <PhoneCall className="w-4 h-4" />
                        </Link>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>

        {/* Recent Calls */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">Recent Calls</h2>
            <Link
              to="/instant"
              className="text-sm text-blue-600 hover:text-blue-700 font-medium flex items-center gap-1"
            >
              View all
              <ArrowUpRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          {recentCalls.length === 0 ? (
            <div className="bg-white border border-gray-200 rounded-xl p-8 text-center">
              <div className="w-10 h-10 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-3">
                <PhoneCall className="w-5 h-5 text-gray-400" />
              </div>
              <h3 className="text-sm font-medium text-gray-900 mb-1">No calls yet</h3>
              <p className="text-xs text-gray-500">Your call history will appear here.</p>
            </div>
          ) : (
            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-gray-200">
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3">Phone</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3">Employee</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3">Status</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3 hidden md:table-cell">Duration</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3 hidden lg:table-cell">Time</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {recentCalls.map(call => {
                    const emp = employees.find(e => e.id === call.employee_id);
                    const statusColor = call.status === 'completed' ? 'text-emerald-600 bg-emerald-50' :
                                       call.status === 'in_progress' ? 'text-blue-600 bg-blue-50' :
                                       call.status === 'no_answer' ? 'text-amber-600 bg-amber-50' :
                                       'text-gray-600 bg-gray-100';
                    return (
                      <tr key={call.id} className="hover:bg-gray-50 transition">
                        <td className="px-4 py-3">
                          <div className="font-medium text-gray-900 text-sm">{call.phone}</div>
                          {call.context && (
                            <div className="text-xs text-gray-500 truncate max-w-[200px]">{call.context}</div>
                          )}
                        </td>
                        <td className="px-4 py-3 hidden md:table-cell">
                          <span className="text-sm text-gray-700">{emp?.name || '—'}</span>
                        </td>
                        <td className="px-4 py-3">
                          <span className={`text-xs px-2 py-0.5 rounded-full ${statusColor}`}>
                            {call.status === 'in_progress' ? 'In progress' :
                             call.status === 'no_answer' ? 'No answer' :
                             call.status === 'completed' && call.is_answered ? 'Answered' :
                             call.status === 'completed' && !call.is_answered ? 'Not answered' : call.status}
                          </span>
                          {call.lead_status && call.status === 'completed' && (
                            <div className="text-xs text-gray-500 mt-0.5">{call.lead_status}</div>
                          )}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-700 hidden md:table-cell">
                          {formatDuration(call.duration || 0)}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-500 hidden lg:table-cell">
                          {formatTime(call.started_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Active Campaigns */}
        {campaigns.filter(c => c.status === 'running').length > 0 && (
          <section>
            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Active Campaigns</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {campaigns.filter(c => c.status === 'running').map(camp => {
                const pct = camp.total_contacts > 0 ? Math.round(camp.completed / camp.total_contacts * 100) : 0;
                return (
                  <div key={camp.id} className="bg-white border border-gray-200 rounded-xl p-4">
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <Megaphone className="w-4 h-4 text-violet-600" />
                        <span className="font-medium text-gray-900 text-sm">{camp.name}</span>
                      </div>
                      <span className="text-xs bg-violet-100 text-violet-700 px-2 py-0.5 rounded-full">{pct}% complete</span>
                    </div>
                    <div className="h-2 bg-gray-200 rounded-full overflow-hidden mb-3">
                      <div
                        className="h-full bg-violet-500 rounded-full transition-all"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <div className="flex items-center justify-between text-xs text-gray-500">
                      <span>{camp.completed} / {camp.total_contacts} contacts</span>
                      <span>{camp.connected} connected · {camp.minutes_used} min used</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

import { useEffect, useState } from 'react';
import { useAuth } from '../../auth/hooks/useAuth';
import { Settings, User, Bell, Shield, ChevronRight, ImagePlus, Trash2 } from 'lucide-react';
import { backendJson } from '../../../services/backend/api';

interface TenantSettings {
  business_name: string;
  logo_data_url: string | null;
  instant_leads_enabled: boolean;
  notify_campaign_completed: boolean;
  notify_low_balance: boolean;
}

export default function SettingsPage() {
  const { user, signOut } = useAuth();
  const [activeSection, setActiveSection] = useState('account');
  const [saved, setSaved] = useState(false);
  const [settings, setSettings] = useState<TenantSettings | null>(null);
  const [notifSaving, setNotifSaving] = useState(false);
  const [businessName, setBusinessName] = useState('');
  const [logoDataUrl, setLogoDataUrl] = useState<string | null>(null);
  const [brandSaving, setBrandSaving] = useState(false);
  const [brandError, setBrandError] = useState('');

  useEffect(() => {
    backendJson<TenantSettings>('/settings')
      .then(data => {
        setSettings(data);
        setBusinessName(data.business_name);
        setLogoDataUrl(data.logo_data_url);
      })
      .catch(() => {/* non-fatal */});
  }, []);

  const handleSave = async () => {
    if (!businessName.trim()) return setBrandError('Business name is required.');
    setBrandSaving(true);
    setBrandError('');
    try {
      const updated = await backendJson<TenantSettings>('/settings', {
        method: 'PATCH',
        body: JSON.stringify({ business_name: businessName.trim(), logo_data_url: logoDataUrl }),
      });
      setSettings(updated);
      setBusinessName(updated.business_name);
      setLogoDataUrl(updated.logo_data_url);
      window.dispatchEvent(new CustomEvent('pontis:branding-updated', { detail: updated }));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (saveError) {
      setBrandError(saveError instanceof Error ? saveError.message : 'Unable to save branding.');
    } finally {
      setBrandSaving(false);
    }
  };

  const chooseLogo = (file: File | undefined) => {
    if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) return setBrandError('Choose a PNG, JPEG, or WebP image.');
    if (file.size > 700_000) return setBrandError('Logo image must be smaller than 700 KB.');
    const reader = new FileReader();
    reader.onload = () => {
      setLogoDataUrl(String(reader.result));
      setBrandError('');
    };
    reader.onerror = () => setBrandError('Unable to read that image.');
    reader.readAsDataURL(file);
  };

  const initials = (() => {
    const words = businessName.trim().split(/\s+/).filter(Boolean);
    if (!words.length) return 'PC';
    return (words.length === 1 ? words[0].slice(0, 2) : `${words[0][0]}${words[1][0]}`).toUpperCase();
  })();

  const saveNotification = async (key: keyof TenantSettings, value: boolean) => {
    if (!settings) return;
    const updated = { ...settings, [key]: value };
    setSettings(updated);
    setNotifSaving(true);
    try {
      const saved = await backendJson<TenantSettings>('/settings', {
        method: 'PATCH',
        body: JSON.stringify({ [key]: value }),
      });
      setSettings(saved);
    } catch {
      setSettings(settings); // revert on error
    } finally {
      setNotifSaving(false);
    }
  };

  const sections = [
    { id: 'account', label: 'Account', icon: User },
    { id: 'notifications', label: 'Notifications', icon: Bell },
    { id: 'security', label: 'Security', icon: Shield },
  ];

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="h-16 border-b border-gray-200 px-6 flex items-center bg-white">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gray-100 flex items-center justify-center">
            <Settings className="w-4 h-4 text-gray-600" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-gray-900">Settings</h1>
            <p className="text-xs text-gray-500">Manage your account and preferences</p>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-hidden flex">
        <div className="w-56 border-r border-gray-200 bg-white flex-shrink-0 overflow-y-auto">
          <nav className="p-3 space-y-1">
            {sections.map(s => (
              <button
                key={s.id}
                onClick={() => setActiveSection(s.id)}
                className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                  activeSection === s.id ? 'bg-blue-50 text-blue-700' : 'text-gray-700 hover:bg-gray-100'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  <s.icon className="w-4 h-4" />
                  {s.label}
                </div>
                <ChevronRight className="w-3.5 h-3.5 opacity-40" />
              </button>
            ))}
          </nav>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {activeSection === 'account' && (
            <div className="max-w-2xl space-y-6">
              <div>
                <h2 className="text-base font-semibold text-gray-900 mb-4">Account Information</h2>
                <div className="bg-white border border-gray-200 rounded-xl divide-y divide-gray-100">
                  <div className="p-4 flex items-center gap-4">
                    <div className="w-14 h-14 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 text-xl font-bold flex-shrink-0">
                      {user?.email?.[0]?.toUpperCase() ?? 'U'}
                    </div>
                    <div>
                      <div className="font-medium text-gray-900">{user?.email ?? 'No email'}</div>
                      <div className="text-xs text-gray-500 mt-0.5">User ID: {user?.id?.slice(0, 8)}...</div>
                    </div>
                  </div>
                  <div className="p-4 space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Email address</label>
                      <input
                        type="email"
                        defaultValue={user?.email ?? ''}
                        className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 transition"
                      />
                    </div>
                  </div>
                </div>
              </div>

              <div>
                <h2 className="mb-1 text-base font-semibold text-gray-900">Business branding</h2>
                <p className="mb-4 text-xs text-gray-500">This name and logo appear at the top of the dashboard sidebar.</p>
                <div className="rounded-xl border border-violet-100 bg-white p-5 shadow-sm">
                  <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
                    {logoDataUrl
                      ? <img src={logoDataUrl} alt="Business logo preview" className="h-20 w-20 rounded-2xl border border-slate-200 object-cover shadow-sm" />
                      : <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-600 to-indigo-600 text-xl font-bold text-white shadow-lg shadow-violet-200">{initials}</div>}
                    <div className="flex-1">
                      <label className="block text-sm font-medium text-gray-700">
                        Business name
                        <input value={businessName} onChange={event => setBusinessName(event.target.value)} maxLength={255} className="mt-1.5 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm outline-none focus:border-violet-500 focus:bg-white focus:ring-4 focus:ring-violet-100" />
                      </label>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-violet-50 px-3 py-2 text-xs font-semibold text-violet-700 transition hover:bg-violet-100">
                          <ImagePlus className="h-4 w-4" /> {logoDataUrl ? 'Replace logo' : 'Upload logo'}
                          <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" onChange={event => chooseLogo(event.target.files?.[0])} />
                        </label>
                        {logoDataUrl && <button type="button" onClick={() => setLogoDataUrl(null)} className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-semibold text-rose-600 hover:bg-rose-50"><Trash2 className="h-4 w-4" /> Remove logo</button>}
                      </div>
                      <p className="mt-2 text-xs text-slate-400">PNG, JPEG, or WebP. Maximum 700 KB.</p>
                    </div>
                  </div>
                  {brandError && <p className="mt-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{brandError}</p>}
                </div>
              </div>

              <div className="flex items-center justify-between">
                <button
                  onClick={() => void handleSave()}
                  disabled={brandSaving || !businessName.trim()}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition"
                >
                  {brandSaving ? 'Saving…' : saved ? 'Saved!' : 'Save changes'}
                </button>
                <button
                  onClick={signOut}
                  className="px-4 py-2 text-sm text-rose-600 hover:bg-rose-50 rounded-lg transition font-medium"
                >
                  Sign out
                </button>
              </div>
            </div>
          )}

          {activeSection === 'notifications' && (
            <div className="max-w-2xl space-y-6">
              <div>
                <h2 className="text-base font-semibold text-gray-900">Notification Preferences</h2>
                <p className="text-xs text-gray-500 mt-1">
                  These preferences are saved to your account and take effect immediately.
                </p>
              </div>
              <div className="bg-white border border-gray-200 rounded-xl divide-y divide-gray-100">
                <NotifRow
                  label="Campaign completed"
                  desc="In-app alert when a bulk campaign finishes running"
                  checked={settings?.notify_campaign_completed ?? true}
                  disabled={notifSaving || !settings}
                  onChange={v => void saveNotification('notify_campaign_completed', v)}
                />
                <NotifRow
                  label="Low balance alert"
                  desc="In-app alert when your wallet balance drops below the minimum threshold"
                  checked={settings?.notify_low_balance ?? true}
                  disabled={notifSaving || !settings}
                  onChange={v => void saveNotification('notify_low_balance', v)}
                />
              </div>
              {notifSaving && <p className="text-xs text-gray-400">Saving…</p>}
            </div>
          )}

          {activeSection === 'security' && (
            <div className="max-w-2xl space-y-6">
              <h2 className="text-base font-semibold text-gray-900">Security</h2>
              <div className="bg-white border border-gray-200 rounded-xl divide-y divide-gray-100">
                <div className="p-4 space-y-4">
                  <h3 className="text-sm font-semibold text-gray-900">Change Password</h3>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Current password</label>
                    <input type="password" placeholder="••••••••" className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 transition" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">New password</label>
                    <input type="password" placeholder="••••••••" className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 transition" />
                  </div>
                  <button className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition">
                    Update password
                  </button>
                </div>
                <div className="p-4">
                  <h3 className="text-sm font-semibold text-gray-900 mb-1">Danger Zone</h3>
                  <p className="text-xs text-gray-500 mb-3">Permanently delete your account and all associated data.</p>
                  <button className="px-4 py-2 border border-rose-300 text-rose-600 hover:bg-rose-50 text-sm font-medium rounded-lg transition">
                    Delete account
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function NotifRow({ label, desc, checked, disabled, onChange }: {
  label: string; desc: string; checked: boolean; disabled: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="p-4 flex items-center justify-between">
      <div>
        <div className="text-sm font-medium text-gray-900">{label}</div>
        <div className="text-xs text-gray-500 mt-0.5">{desc}</div>
      </div>
      <label className="relative inline-flex items-center cursor-pointer">
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={e => onChange(e.target.checked)}
          className="sr-only peer"
        />
        <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-blue-600 disabled:opacity-50" />
      </label>
    </div>
  );
}

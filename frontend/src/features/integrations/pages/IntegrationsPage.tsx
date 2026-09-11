import { useEffect, useState } from 'react';
import { Puzzle, CheckCircle2, XCircle, Clock, Webhook, Key, Lock } from 'lucide-react';
import { backendJson, backendFetch } from '../../../services/backend/api';

interface Integration {
  key: string;
  name: string;
  category: string;
  description: string;
  connection_mode: 'webhook_url' | 'api_key' | 'oauth' | 'coming_soon';
  status: 'connected' | 'not_connected' | 'pending' | 'error';
  external_account_reference: string | null;
  connected_at: string | null;
}

const CATEGORY_ORDER = ['CRM', 'Scheduling', 'Notifications', 'Automation', 'Developer', 'Messaging'];

const ICONS: Record<string, string> = {
  hubspot: '🟠', salesforce: '☁️', ghl: '📊',
  google_calendar: '📆', cal_com: '📅',
  slack: '💬', make: '⚙️', zapier: '⚡', n8n: '🔄',
  custom_api: '🔌', whatsapp: '📱',
};

type ConnectModal =
  | { type: 'webhook_url'; key: string; name: string }
  | { type: 'api_key'; key: string; name: string }
  | null;

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [modal, setModal] = useState<ConnectModal>(null);
  const [inputValue, setInputValue] = useState('');
  const [submitting, setSubmitting] = useState('');
  const [disconnecting, setDisconnecting] = useState('');

  const load = () =>
    backendJson<Integration[]>('/integrations')
      .then(data => setIntegrations(data ?? []))
      .catch(() => setError('Unable to load integrations.'))
      .finally(() => setLoading(false));

  useEffect(() => { void load(); }, []);

  // Handle ?connected=key after OAuth redirect
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get('connected');
    if (!connected) return;
    window.history.replaceState({}, '', window.location.pathname);
    void load().then(() => setNotice(`${connected} connected successfully.`));
  }, []);

  const handleConnect = async (integration: Integration) => {
    if (integration.connection_mode === 'coming_soon') return;
    if (integration.connection_mode === 'oauth') {
      setSubmitting(integration.key);
      try {
        const { authorization_url } = await backendJson<{ authorization_url: string }>(
          `/integrations/${integration.key}/connect/oauth/init`,
          { method: 'POST' }
        );
        // Navigate to provider OAuth page — must not be omnidim.io
        const anchor = document.createElement('a');
        anchor.href = authorization_url;
        anchor.rel = 'noopener noreferrer';
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
      } catch {
        setError(`Unable to start ${integration.name} connection.`);
        setSubmitting('');
      }
      return;
    }
    setInputValue('');
    setModal({ type: integration.connection_mode as 'webhook_url' | 'api_key', key: integration.key, name: integration.name });
  };

  const handleModalSubmit = async () => {
    if (!modal || !inputValue.trim()) return;
    setSubmitting(modal.key);
    try {
      const endpoint = modal.type === 'webhook_url'
        ? `/integrations/${modal.key}/connect/webhook`
        : `/integrations/${modal.key}/connect/api-key`;
      const body = modal.type === 'webhook_url'
        ? { webhook_url: inputValue.trim() }
        : { api_key: inputValue.trim() };
      await backendJson(`${endpoint}`, { method: 'POST', body: JSON.stringify(body) });
      setNotice(`${modal.name} connected.`);
      setModal(null);
      void load();
    } catch {
      setError(`Unable to connect ${modal.name}. Check the value and try again.`);
    } finally {
      setSubmitting('');
    }
  };

  const handleDisconnect = async (integration: Integration) => {
    setDisconnecting(integration.key);
    setError('');
    try {
      await backendFetch(`/integrations/${integration.key}`, { method: 'DELETE' });
      setNotice(`${integration.name} disconnected.`);
      void load();
    } catch {
      setError(`Unable to disconnect ${integration.name}.`);
    } finally {
      setDisconnecting('');
    }
  };

  const grouped = CATEGORY_ORDER.reduce<Record<string, Integration[]>>((acc, cat) => {
    const items = integrations.filter(i => i.category === cat);
    if (items.length) acc[cat] = items;
    return acc;
  }, {});
  integrations.forEach(i => {
    if (!grouped[i.category]) grouped[i.category] = integrations.filter(x => x.category === i.category);
  });

  if (loading) {
    return (
      <div className="flex-1 bg-slate-50 overflow-y-auto">
        <header className="h-16 bg-white border-b border-gray-200 px-6 flex items-center">
          <div className="h-5 w-32 bg-gray-200 rounded animate-pulse" />
        </header>
        <main className="max-w-5xl mx-auto p-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="h-40 bg-white border border-gray-200 rounded-2xl animate-pulse" />
            ))}
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="flex-1 bg-slate-50 overflow-y-auto">
      <header className="h-16 bg-white border-b border-gray-200 px-6 flex items-center">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center">
            <Puzzle className="w-4 h-4 text-indigo-600" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-gray-900">Integrations</h1>
            <p className="text-xs text-gray-500">Connect your AI employees to external tools</p>
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto p-6 space-y-8">
        {(error || notice) && (
          <p className={`px-4 py-3 text-sm rounded-xl border ${
            error ? 'bg-rose-50 border-rose-200 text-rose-700' : 'bg-emerald-50 border-emerald-200 text-emerald-700'
          }`}>
            {error || notice}
          </p>
        )}

        {Object.entries(grouped).map(([category, items]) => (
          <section key={category}>
            <h2 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">{category}</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {items.map(integration => {
                const isConnected = integration.status === 'connected';
                const isComingSoon = integration.connection_mode === 'coming_soon';
                const isBusy = submitting === integration.key || disconnecting === integration.key;
                return (
                  <article
                    key={integration.key}
                    className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm flex flex-col gap-3 hover:shadow-md transition-shadow"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex items-center gap-3">
                        <span className="text-2xl">{ICONS[integration.key] || '🔗'}</span>
                        <div>
                          <div className="font-semibold text-gray-900 text-sm">{integration.name}</div>
                          <div className="text-xs text-gray-500">{integration.category}</div>
                        </div>
                      </div>
                      <StatusBadge status={integration.status} isComingSoon={isComingSoon} />
                    </div>

                    <p className="text-xs text-gray-500 leading-relaxed flex-1">{integration.description}</p>

                    {isConnected && integration.external_account_reference && (
                      <p className="text-xs text-gray-400 truncate">{integration.external_account_reference}</p>
                    )}

                    <div className="flex items-center gap-2 pt-1 border-t border-gray-100">
                      {isComingSoon ? (
                        <span className="flex-1 text-xs text-center text-gray-400 py-1.5">Coming soon</span>
                      ) : isConnected ? (
                        <>
                          <ModeIcon mode={integration.connection_mode} />
                          <button
                            onClick={() => void handleDisconnect(integration)}
                            disabled={isBusy}
                            className="flex-1 text-xs font-medium py-1.5 rounded-lg border border-rose-200 text-rose-600 hover:bg-rose-50 transition disabled:opacity-50"
                          >
                            {disconnecting === integration.key ? '…' : 'Disconnect'}
                          </button>
                        </>
                      ) : (
                        <button
                          onClick={() => void handleConnect(integration)}
                          disabled={isBusy}
                          className="flex-1 text-xs font-medium py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition disabled:opacity-50"
                        >
                          {submitting === integration.key ? '…' : 'Connect'}
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
        ))}
      </main>

      {/* Connect modal for webhook / api-key integrations */}
      {modal && (
        <div className="fixed inset-0 z-50 bg-black/40 grid place-items-center p-4">
          <section className="bg-white rounded-2xl max-w-md w-full p-6 shadow-xl border border-gray-200">
            <h2 className="font-semibold text-lg text-gray-900 mb-1">Connect {modal.name}</h2>
            <p className="text-sm text-gray-500 mb-4">
              {modal.type === 'webhook_url'
                ? 'Paste your webhook URL. Call results will be posted to this endpoint.'
                : 'Enter your API key. It will be stored securely and never shown again.'}
            </p>
            <input
              type={modal.type === 'api_key' ? 'password' : 'url'}
              placeholder={modal.type === 'webhook_url' ? 'https://hooks.example.com/...' : 'Your API key'}
              value={inputValue}
              onChange={e => setInputValue(e.target.value)}
              className="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-blue-500"
              autoFocus
            />
            <div className="flex gap-2">
              <button
                onClick={() => setModal(null)}
                className="flex-1 border border-gray-200 rounded-xl py-2 text-sm text-gray-600 hover:bg-gray-50 transition"
              >
                Cancel
              </button>
              <button
                onClick={() => void handleModalSubmit()}
                disabled={!inputValue.trim() || submitting === modal.key}
                className="flex-1 bg-blue-600 hover:bg-blue-700 text-white rounded-xl py-2 text-sm font-medium transition disabled:opacity-50"
              >
                {submitting === modal.key ? '…' : 'Save'}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status, isComingSoon }: { status: string; isComingSoon: boolean }) {
  if (isComingSoon) return (
    <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-gray-100 text-gray-400 flex-shrink-0 flex items-center gap-1">
      <Clock className="w-3 h-3" /> Soon
    </span>
  );
  if (status === 'connected') return (
    <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-emerald-100 text-emerald-700 flex-shrink-0 flex items-center gap-1">
      <CheckCircle2 className="w-3 h-3" /> Connected
    </span>
  );
  if (status === 'error') return (
    <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-rose-100 text-rose-600 flex-shrink-0 flex items-center gap-1">
      <XCircle className="w-3 h-3" /> Error
    </span>
  );
  return (
    <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-gray-100 text-gray-500 flex-shrink-0">
      Not connected
    </span>
  );
}

function ModeIcon({ mode }: { mode: string }) {
  if (mode === 'webhook_url') return <Webhook className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />;
  if (mode === 'api_key') return <Key className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />;
  if (mode === 'oauth') return <Lock className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />;
  return null;
}

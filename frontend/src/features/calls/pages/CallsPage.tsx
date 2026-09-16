import { useEffect, useState } from 'react';
import { Phone, PhoneIncoming, PhoneOutgoing, X, Play, ChevronRight } from 'lucide-react';
import { backendJson } from '../../../services/backend/api';

interface CallRecord {
  id: string;
  employee_id: string;
  campaign_id: string | null;
  direction: string;
  status: string;
  customer_name: string | null;
  customer_phone_number: string | null;
  duration_seconds: number | null;
  recording_url: string | null;
  transcript: string | null;
  summary: string | null;
  sentiment: string | null;
  extracted_attributes: Record<string, unknown> | null;
  outcome: string | null;
  started_at: string | null;
  ended_at: string | null;
  analysis_status: string | null;
  customer_intent: string | null;
  key_points: string[] | null;
  action_items: string[] | null;
  follow_up_required: boolean | null;
  follow_up_notes: string | null;
  created_at: string;
}

const STATUS_COLORS: Record<string, string> = {
  completed: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-rose-100 text-rose-700',
  no_answer: 'bg-amber-100 text-amber-700',
  busy: 'bg-amber-100 text-amber-700',
  in_progress: 'bg-blue-100 text-blue-700',
  queued: 'bg-gray-100 text-gray-600',
  canceled: 'bg-gray-100 text-gray-500',
  voicemail: 'bg-purple-100 text-purple-700',
};

const SENTIMENT_COLORS: Record<string, string> = {
  positive: 'text-emerald-600',
  negative: 'text-rose-600',
  neutral: 'text-gray-500',
};

function fmt(seconds: number | null): string {
  if (seconds == null) return '—';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit',
  });
}

function parseTranscript(raw: string): Array<{ speaker: string; text: string }> {
  const lines = raw.split('\n').filter(l => l.trim());
  return lines.map(line => {
    const match = line.match(/^(user|llm|agent|ai|customer|bot):\s*/i);
    if (match) {
      const speaker = match[1].toLowerCase();
      const label = ['user', 'customer'].includes(speaker) ? 'Customer' : 'AI Employee';
      return { speaker: label, text: line.slice(match[0].length).trim() };
    }
    return { speaker: '', text: line.trim() };
  });
}

function CallDetailDrawer({ call, onClose }: { call: CallRecord; onClose: () => void }) {
  const transcript = call.transcript ? parseTranscript(call.transcript) : null;
  const sentimentKey = (call.sentiment || '').toLowerCase();
  const sentimentColor = SENTIMENT_COLORS[sentimentKey] || 'text-gray-500';

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="relative w-full max-w-xl bg-white shadow-2xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <div className="flex items-center gap-3">
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${call.direction === 'inbound' ? 'bg-blue-50' : 'bg-emerald-50'}`}>
              {call.direction === 'inbound'
                ? <PhoneIncoming className="w-4 h-4 text-blue-600" />
                : <PhoneOutgoing className="w-4 h-4 text-emerald-600" />}
            </div>
            <div>
              <div className="font-semibold text-gray-900 text-sm">
                {call.customer_name || call.customer_phone_number || 'Unknown'}
              </div>
              <div className="text-xs text-gray-500 font-mono">{call.customer_phone_number}</div>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-400">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {/* Overview */}
          <section className="bg-gray-50 rounded-xl p-4 grid grid-cols-2 gap-3 text-sm">
            {[
              ['Direction', call.direction === 'inbound' ? 'Inbound' : 'Outbound'],
              ['Status', call.status.replace(/_/g, ' ')],
              ['Duration', fmt(call.duration_seconds)],
              ['Date', fmtDate(call.started_at || call.created_at)],
              ['Ended', fmtDate(call.ended_at)],
              ['Outcome', call.outcome || '—'],
            ].map(([label, value]) => (
              <div key={label}>
                <div className="text-xs text-gray-500">{label}</div>
                <div className="font-medium text-gray-900 capitalize">{value}</div>
              </div>
            ))}
          </section>

          {/* Sentiment */}
          {call.sentiment && (
            <section>
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Sentiment</h3>
              <span className={`text-sm font-medium capitalize ${sentimentColor}`}>{call.sentiment}</span>
            </section>
          )}

          {/* Summary */}
          {call.summary && (
            <section>
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Summary</h3>
              <p className="text-sm text-gray-700 leading-relaxed">{call.summary}</p>
            </section>
          )}

          {(call.customer_intent || call.key_points?.length || call.action_items?.length || call.follow_up_required) && (
            <section>
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">AI analysis</h3>
              {call.customer_intent && <p className="text-sm text-gray-700"><span className="font-medium">Intent:</span> {call.customer_intent}</p>}
              {!!call.key_points?.length && <p className="mt-2 text-sm text-gray-700"><span className="font-medium">Key points:</span> {call.key_points.join(' • ')}</p>}
              {!!call.action_items?.length && <p className="mt-2 text-sm text-gray-700"><span className="font-medium">Actions:</span> {call.action_items.join(' • ')}</p>}
              {call.follow_up_required && <p className="mt-2 text-sm text-amber-700">Follow-up required{call.follow_up_notes ? `: ${call.follow_up_notes}` : ''}</p>}
            </section>
          )}

          {/* Recording */}
          {call.recording_url && (
            <section>
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Recording</h3>
              <a
                href={call.recording_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-2 px-3 py-2 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-700 hover:bg-blue-100 transition"
              >
                <Play className="w-3.5 h-3.5" /> Play recording
              </a>
            </section>
          )}

          {/* Transcript */}
          {transcript && transcript.length > 0 && (
            <section>
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Transcript</h3>
              <div className="space-y-2">
                {transcript.map((line, i) => (
                  <div key={i} className={`flex gap-2 ${line.speaker === 'Customer' ? 'justify-start' : 'justify-end'}`}>
                    <div className={`max-w-[80%] px-3 py-2 rounded-xl text-sm leading-relaxed ${
                      line.speaker === 'Customer'
                        ? 'bg-gray-100 text-gray-800'
                        : line.speaker === 'AI Employee'
                        ? 'bg-blue-600 text-white'
                        : 'bg-gray-50 text-gray-600 italic'
                    }`}>
                      {line.speaker && <div className="text-xs opacity-60 mb-0.5">{line.speaker}</div>}
                      {line.text}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Extracted variables */}
          {call.extracted_attributes && Object.keys(call.extracted_attributes).length > 0 && (
            <section>
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Extracted Information</h3>
              <div className="bg-gray-50 rounded-xl divide-y divide-gray-200 border border-gray-200">
                {Object.entries(call.extracted_attributes).map(([key, val]) => (
                  <div key={key} className="px-3 py-2 flex items-start gap-3">
                    <span className="text-xs text-gray-500 w-32 flex-shrink-0 capitalize">{key.replace(/_/g, ' ')}</span>
                    <span className="text-sm text-gray-900">{String(val)}</span>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      </aside>
    </div>
  );
}

export default function CallsPage() {
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<CallRecord | null>(null);

  useEffect(() => {
    backendJson<CallRecord[]>('/calls')
      .then(data => setCalls(data || []))
      .catch(() => setError('Unable to load calls.'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!calls.some(call => call.status === 'queued' || call.status === 'ringing' || call.status === 'in_progress')) return;
    const timer = window.setInterval(() => {
      void Promise.all(calls.filter(call => ['queued', 'ringing', 'in_progress'].includes(call.status)).map(call => backendJson<CallRecord>(`/calls/${call.id}/refresh`, { method: 'POST' })))
        .then(updated => setCalls(current => current.map(call => updated.find(item => item.id === call.id) ?? call)))
        .catch(() => undefined);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [calls]);

  if (loading) {
    return (
      <div className="flex-1 bg-slate-50 overflow-y-auto">
        <header className="h-16 bg-white border-b border-gray-200 px-6 flex items-center">
          <div className="h-5 w-24 bg-gray-200 rounded animate-pulse" />
        </header>
        <main className="max-w-5xl mx-auto p-6 space-y-3">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-16 bg-white border border-gray-200 rounded-xl animate-pulse" />
          ))}
        </main>
      </div>
    );
  }

  return (
    <div className="flex-1 bg-slate-50 overflow-y-auto">
      <header className="h-16 bg-white border-b border-gray-200 px-6 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-gray-900">Calls</h1>
          <p className="text-xs text-gray-500">{calls.length} call{calls.length === 1 ? '' : 's'}</p>
        </div>
      </header>

      <main className="max-w-5xl mx-auto p-6">
        {error && (
          <p className="mb-4 px-4 py-3 text-sm rounded-xl border bg-rose-50 border-rose-200 text-rose-700">{error}</p>
        )}

        {calls.length === 0 ? (
          <div className="bg-white border border-gray-200 rounded-2xl p-16 text-center shadow-sm">
            <div className="w-14 h-14 bg-blue-50 rounded-2xl grid place-items-center mx-auto mb-4">
              <Phone className="w-7 h-7 text-blue-500" />
            </div>
            <h2 className="text-base font-semibold text-gray-900">No calls yet</h2>
            <p className="mt-1 text-sm text-gray-500">Calls will appear here after they are completed.</p>
          </div>
        ) : (
          <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
            <table className="w-full">
              <thead className="border-b border-gray-200 bg-gray-50">
                <tr>
                  <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3">Contact</th>
                  <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3 hidden sm:table-cell">Direction</th>
                  <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3">Status</th>
                  <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3 hidden md:table-cell">Duration</th>
                  <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3 hidden lg:table-cell">Date</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {calls.map(call => {
                  const statusColor = STATUS_COLORS[call.status] || 'bg-gray-100 text-gray-600';
                  return (
                    <tr
                      key={call.id}
                      className="hover:bg-gray-50 cursor-pointer transition"
                      onClick={() => setSelected(call)}
                    >
                      <td className="px-4 py-3">
                        <div className="font-medium text-gray-900 text-sm">{call.customer_name || '—'}</div>
                        <div className="font-mono text-xs text-gray-500">{call.customer_phone_number || '—'}</div>
                      </td>
                      <td className="px-4 py-3 hidden sm:table-cell">
                        <div className="flex items-center gap-1.5 text-sm text-gray-600">
                          {call.direction === 'inbound'
                            ? <PhoneIncoming className="w-3.5 h-3.5 text-blue-500" />
                            : <PhoneOutgoing className="w-3.5 h-3.5 text-emerald-500" />}
                          <span className="capitalize">{call.direction}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusColor}`}>
                          {call.status.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-700 hidden md:table-cell">{fmt(call.duration_seconds)}</td>
                      <td className="px-4 py-3 text-sm text-gray-500 hidden lg:table-cell">{fmtDate(call.started_at || call.created_at)}</td>
                      <td className="px-4 py-3">
                        <ChevronRight className="w-4 h-4 text-gray-400" />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </main>

      {selected && <CallDetailDrawer call={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

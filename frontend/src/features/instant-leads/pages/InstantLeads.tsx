import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Loader2, PhoneCall, RefreshCw, ToggleLeft, ToggleRight } from 'lucide-react';
import { backendFetch, backendJson } from '../../../services/backend/api';
import { CountryCodeSelect } from '../../../components/forms/CountryCodeSelect';

interface Employee { id: string; name: string; status: string; provider_agent_id?: string | null }
interface PhoneNumber { id: string; e164_number: string; provider_name: string | null; status: string }
interface CallResult {
  id: string; employee_id: string; customer_phone_number: string; provider_call_id: string | null;
  status: string; created_at: string; duration_seconds?: number | null; recording_url?: string | null;
  transcript?: string | null; summary?: string | null; sentiment?: string | null;
  extracted_attributes?: Record<string, unknown> | null; ended_at?: string | null;
}
interface TenantSettings { instant_leads_enabled: boolean }
interface LeadSource { id: string; filename: string; enabled: boolean; name?: string; spreadsheet_id?: string | null; sheet_name?: string | null; timezone?: string | null; frequency_minutes?: number; auto_call?: boolean; working_hours?: { start?: string; end?: string }; daily_call_limit?: number | null; last_checked_at?: string | null; last_success_at?: string | null; last_status?: string | null; new_rows?: number; last_result?: { records_checked?: number; new_leads?: number; calls_queued?: number; skipped_duplicates?: number } }

export default function InstantLeads() {
  const [searchParams] = useSearchParams();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [employeeId, setEmployeeId] = useState(searchParams.get('employee') || '');
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [destination, setDestination] = useState('');
  const [destinationCode, setDestinationCode] = useState('+91');
  const [customerName, setCustomerName] = useState('');
  const [context, setContext] = useState('');
  const [result, setResult] = useState<CallResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [calling, setCalling] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [togglingEnabled, setTogglingEnabled] = useState(false);
  const [source, setSource] = useState<LeadSource | null>(null);
  const [uploading, setUploading] = useState(false);
  const [selectedFilename, setSelectedFilename] = useState('');
  const [checking, setChecking] = useState(false);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const [employeeData, numberData, settings, sourceData] = await Promise.all([
          backendJson<Employee[]>('/employees'),
          backendJson<PhoneNumber[]>('/phone-numbers'),
          backendJson<TenantSettings>('/settings'),
          backendJson<LeadSource | null>('/instant-leads/source'),
        ]);
        setEmployees(employeeData.filter(e => e.status === 'published' && e.provider_agent_id));
        setNumbers(numberData.filter(n => n.status === 'active'));
        setEnabled(settings.instant_leads_enabled);
        setSource(sourceData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unable to load calling options.');
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  const toggleEnabled = async () => {
    setTogglingEnabled(true);
    setError('');
    try {
      const updated = await backendJson<TenantSettings>('/settings', {
        method: 'PATCH',
        body: JSON.stringify({ instant_leads_enabled: !enabled }),
      });
      setEnabled(updated.instant_leads_enabled);
    } catch {
      setError('Unable to update setting. Please try again.');
    } finally {
      setTogglingEnabled(false);
    }
  };

  const uploadSource = async (file: File) => {
    if (!employeeId || !phoneNumberId) {
      setError('Select a published employee and caller number before uploading a lead document.');
      return;
    }
    setUploading(true); setError('');
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('employee_id', employeeId);
      form.append('phone_number_id', phoneNumberId);
      const response = await backendFetch('/instant-leads/source', { method: 'POST', body: form });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : 'Unable to upload the lead document.');
      setSource(body as LeadSource);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to upload the lead document.');
    } finally { setUploading(false); }
  };

  const toggleSource = async () => {
    if (!source) return;
    try {
      setSource(await backendJson<LeadSource>(`/instant-leads/source/${source.id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !source.enabled }) }));
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to update document monitoring.'); }
  };

  const checkSource = async () => {
    if (!source) return;
    setChecking(true); setError('');
    try {
      setSource(await backendJson<LeadSource>(`/instant-leads/source/${source.id}/check`, { method: 'POST' }));
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to check the lead document.'); }
    finally { setChecking(false); }
  };

  const testSource = async () => {
    if (!source) return;
    setTesting(true); setError('');
    try {
      const result = await backendJson<{ ok: boolean; message: string }>(`/instant-leads/source/${source.id}/test`, { method: 'POST' });
      if (!result.ok) setError(result.message); else setSource({ ...source, last_status: result.message });
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to test the source.'); }
    finally { setTesting(false); }
  };

  const saveSource = async (changes: Partial<LeadSource>) => {
    if (!source) return;
    try { setSource(await backendJson<LeadSource>(`/instant-leads/source/${source.id}`, { method: 'PATCH', body: JSON.stringify(changes) })); setError(''); }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to save source configuration.'); }
  };

  const refreshResult = async () => {
    if (!result) return;
    setRefreshing(true);
    try {
      setResult(await backendJson<CallResult>(`/calls/${result.id}`));
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to refresh call result.');
    } finally {
      setRefreshing(false);
    }
  };

  const dispatch = async () => {
    const normalizedDestination = `${destinationCode}${destination}`.replace(/[ ()-]/g, '');
    if (!employeeId || !phoneNumberId || !destination.trim()) {
      setError('Select a published employee and caller number, then enter a customer phone number.');
      return;
    }
    if (!/^\+[1-9]\d{6,14}$/.test(normalizedDestination)) {
      setError('Use an E.164 customer phone number, for example +15551234567.');
      return;
    }
    setCalling(true);
    setError('');
    setResult(null);
    try {
      const call = await backendJson<CallResult>('/calls/instant', {
        method: 'POST',
        body: JSON.stringify({
          employee_id: employeeId,
          phone_number_id: phoneNumberId,
          destination_phone_number: normalizedDestination,
          customer_name: customerName || undefined,
          context: context || undefined,
        }),
      });
      setResult(call);
      setDestination('');
      setCustomerName('');
      setContext('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to dispatch the call.');
    } finally {
      setCalling(false);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-gray-500">
        Loading calling options...
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="h-16 border-b border-gray-200 px-6 flex items-center bg-white">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-emerald-100 flex items-center justify-center">
            <PhoneCall className="w-4 h-4 text-emerald-600" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-gray-900">Instant Leads</h1>
            <p className="text-xs text-gray-500">Dispatch one outbound call through your published AI employee</p>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-2xl mx-auto space-y-4">
          {/* ON/OFF Toggle card */}
          <div className="bg-white border border-gray-200 rounded-xl p-4 flex items-center justify-between gap-4">
            <div>
              <div className="text-sm font-semibold text-gray-900">Instant Leads</div>
              <div className="text-xs text-gray-500 mt-0.5">
                {enabled
                  ? 'Enabled — outbound calls can be dispatched immediately.'
                  : 'Disabled — no new instant calls will be dispatched until re-enabled.'}
              </div>
            </div>
            <button
              onClick={() => void toggleEnabled()}
              disabled={togglingEnabled}
              className="flex items-center gap-2 disabled:opacity-50 transition"
              title={enabled ? 'Turn off Instant Leads' : 'Turn on Instant Leads'}
            >
              {togglingEnabled
                ? <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
                : enabled
                ? <ToggleRight className="w-10 h-10 text-emerald-500" />
                : <ToggleLeft className="w-10 h-10 text-gray-400" />}
              <span className={`text-sm font-semibold ${enabled ? 'text-emerald-600' : 'text-gray-400'}`}>
                {enabled ? 'ON' : 'OFF'}
              </span>
            </button>
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-3">
            <div>
              <div className="text-sm font-semibold text-gray-900">Lead document monitoring</div>
              <div className="text-xs text-gray-500 mt-0.5">Upload a CSV/XLSX once. New rows are checked periodically and existing rows are never called again.</div>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <input id="instant-leads-file" type="file" accept=".csv,.xlsx" disabled={uploading || !enabled} onChange={e => { const file = e.target.files?.[0]; setSelectedFilename(file?.name || ''); if (file) void uploadSource(file); }} className="sr-only" />
              <label
                htmlFor="instant-leads-file"
                className={`inline-flex items-center rounded-lg border border-emerald-600 bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-emerald-700 focus-within:outline-none focus-within:ring-2 focus-within:ring-emerald-500 focus-within:ring-offset-2 ${uploading || !enabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'}`}
              >
                {uploading ? 'Uploading…' : 'Choose File'}
              </label>
              <span className="text-sm text-gray-600 truncate max-w-full" aria-live="polite">
                {selectedFilename || source?.filename || 'No file selected'}
              </span>
            </div>
            {source && <div className="text-xs text-gray-600 space-y-1">
              <div><strong>Source:</strong> {source.filename} · {source.enabled ? 'monitoring ON' : 'monitoring OFF'}</div>
              <div><strong>Status:</strong> {source.last_status || 'Not checked yet.'}</div>
              <div><strong>Frequency:</strong> every {source.frequency_minutes || 5} minute(s) · <strong>Auto-call:</strong> {source.auto_call === false ? 'OFF' : 'ON'}</div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-2">
                <input defaultValue={source.name || ''} placeholder="Source name" onBlur={e => void saveSource({ name: e.target.value })} className="rounded border border-gray-300 px-2 py-1.5" />
                <input defaultValue={source.spreadsheet_id || ''} placeholder="Spreadsheet ID" onBlur={e => void saveSource({ spreadsheet_id: e.target.value })} className="rounded border border-gray-300 px-2 py-1.5" />
                <input defaultValue={source.sheet_name || ''} placeholder="Sheet/tab name" onBlur={e => void saveSource({ sheet_name: e.target.value })} className="rounded border border-gray-300 px-2 py-1.5" />
                <select value={source.frequency_minutes || 5} onChange={e => void saveSource({ frequency_minutes: Number(e.target.value) })} className="rounded border border-gray-300 px-2 py-1.5"><option value="1">Every 1 minute</option><option value="5">Every 5 minutes</option><option value="15">Every 15 minutes</option><option value="30">Every 30 minutes</option></select>
                <input defaultValue={source.working_hours?.start || ''} placeholder="Working start (09:00)" onBlur={e => void saveSource({ working_hours: { ...(source.working_hours || {}), start: e.target.value } })} className="rounded border border-gray-300 px-2 py-1.5" />
                <input defaultValue={source.working_hours?.end || ''} placeholder="Working end (18:00)" onBlur={e => void saveSource({ working_hours: { ...(source.working_hours || {}), end: e.target.value } })} className="rounded border border-gray-300 px-2 py-1.5" />
                <input defaultValue={source.timezone || ''} placeholder="Timezone (Asia/Calcutta)" onBlur={e => void saveSource({ timezone: e.target.value })} className="rounded border border-gray-300 px-2 py-1.5" />
                <input type="number" min="0" defaultValue={source.daily_call_limit ?? ''} placeholder="Daily call limit" onBlur={e => void saveSource({ daily_call_limit: e.target.value ? Number(e.target.value) : null })} className="rounded border border-gray-300 px-2 py-1.5" />
              </div>
              {source.last_result && <div><strong>Last result:</strong> {source.last_result.records_checked || 0} checked, {source.last_result.new_leads || 0} new, {source.last_result.calls_queued || 0} queued, {source.last_result.skipped_duplicates || 0} duplicate(s)</div>}
              {source.last_checked_at && <div><strong>Last checked:</strong> {new Date(source.last_checked_at).toLocaleString()}</div>}
              <div className="flex flex-wrap gap-3 pt-1">
                <button onClick={() => void toggleSource()} disabled={!enabled} className="rounded-lg border border-blue-200 px-3 py-1.5 font-semibold text-blue-700 hover:bg-blue-50 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50">Turn monitoring {source.enabled ? 'OFF' : 'ON'}</button>
                <button onClick={() => void checkSource()} disabled={checking || !enabled} className="rounded-lg border border-gray-300 px-3 py-1.5 font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-50">{checking ? 'Checking…' : 'Check now'}</button>
                <button onClick={() => void testSource()} disabled={testing || !enabled} className="rounded-lg border border-gray-300 px-3 py-1.5 font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50">{testing ? 'Testing…' : 'Test connection'}</button>
              </div>
            </div>}
          </div>

          {/* Dispatch form */}
          <div className={`bg-white border border-gray-200 rounded-xl p-5 space-y-4 ${!enabled ? 'opacity-50 pointer-events-none' : ''}`}>
            {error && (
              <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-sm text-rose-700">{error}</div>
            )}
            {result && (
              <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-lg text-sm text-emerald-800">
                <div className="flex items-center justify-between gap-3">
                  <strong>Call dispatched</strong>
                  <button
                    onClick={() => void refreshResult()}
                    disabled={refreshing}
                    className="inline-flex items-center gap-1 text-emerald-700 hover:text-emerald-900 disabled:opacity-50"
                  >
                    {refreshing ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                    Refresh
                  </button>
                </div>
                <div className="mt-1">Reference: <span className="font-mono">{result.id}</span> — Status: {result.status}</div>
                {result.duration_seconds != null && <div>Duration: {result.duration_seconds}s</div>}
                {result.sentiment && <div>Sentiment: {result.sentiment}</div>}
                {result.summary && <div className="mt-2"><strong>Summary:</strong> {result.summary}</div>}
                {result.transcript && <div className="mt-2 whitespace-pre-wrap"><strong>Transcript:</strong> {result.transcript}</div>}
                {result.recording_url && (
                  <a className="inline-block mt-2 underline" href={result.recording_url} target="_blank" rel="noreferrer">
                    Open recording
                  </a>
                )}
                {result.extracted_attributes && (
                  <pre className="mt-2 overflow-auto text-xs">{JSON.stringify(result.extracted_attributes, null, 2)}</pre>
                )}
              </div>
            )}

            <label className="block text-sm font-medium text-gray-700">
              Published AI employee
              <select
                value={employeeId}
                onChange={e => setEmployeeId(e.target.value)}
                disabled={calling}
                className="mt-1 w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm bg-white"
              >
                <option value="">Select employee...</option>
                {employees.map(emp => <option key={emp.id} value={emp.id}>{emp.name}</option>)}
              </select>
            </label>

            <label className="block text-sm font-medium text-gray-700">
              Caller number
              <select
                value={phoneNumberId}
                onChange={e => setPhoneNumberId(e.target.value)}
                disabled={calling}
                className="mt-1 w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm bg-white"
              >
                <option value="">Select assigned number...</option>
                {numbers.map(n => <option key={n.id} value={n.id}>{n.e164_number} — {n.provider_name || 'Provider'}</option>)}
              </select>
            </label>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="block text-sm font-medium text-gray-700">
                Customer name
                <input
                  value={customerName}
                  onChange={e => setCustomerName(e.target.value)}
                  disabled={calling}
                  className="mt-1 w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm"
                />
              </label>
              <label className="block text-sm font-medium text-gray-700">
                  Customer phone *
                <div className="mt-1 flex gap-2"><CountryCodeSelect value={destinationCode} onChange={setDestinationCode} disabled={calling} /><input
                  value={destination}
                  onChange={e => setDestination(e.target.value)}
                  disabled={calling}
                  placeholder="98765 43210"
                  className="mt-1 min-w-0 flex-1 w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm font-mono"
                /></div>
              </label>
            </div>

            <label className="block text-sm font-medium text-gray-700">
              Context <span className="font-normal text-gray-400">(optional)</span>
              <textarea
                value={context}
                onChange={e => setContext(e.target.value)}
                disabled={calling}
                maxLength={2000}
                placeholder="A short note for the AI employee"
                className="mt-1 w-full h-24 px-3 py-2.5 border border-gray-300 rounded-lg text-sm resize-none"
              />
            </label>

            <button
              onClick={() => void dispatch()}
              disabled={calling || !employees.length || !numbers.length}
              className="w-full py-2.5 bg-emerald-600 hover:bg-emerald-700 disabled:bg-emerald-300 text-white font-medium rounded-lg text-sm flex items-center justify-center gap-2"
            >
              {calling
                ? <><Loader2 className="w-4 h-4 animate-spin" />Dispatching...</>
                : <><PhoneCall className="w-4 h-4" />Call Now</>}
            </button>

            {!employees.length && (
              <p className="text-xs text-amber-700">Publish and connect an AI employee before placing a call.</p>
            )}
            {!numbers.length && (
              <p className="text-xs text-amber-700">An assigned active phone number is required before placing a call.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

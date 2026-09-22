import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Megaphone, Users, CheckCircle, XCircle, Clock, PhoneCall,
  Upload, X, AlertTriangle, ChevronLeft, Play, Pause, Square,
  RotateCcw, Phone,
  History, Pencil, Trash2, Save,
} from 'lucide-react';
import { backendJson, backendFetch } from '../../../services/backend/api';

interface EmployeeSummary {
  id: string; name: string; purpose: string; language: string; status: string; is_ready: boolean;
}
interface CampaignProgress {
  total: number; pending: number; in_progress: number; completed: number; failed: number; skipped: number; retry_pending?: number; cancelled?: number;
}
interface CampaignDetail {
  id: string; name: string; description: string | null; status: string;
  employee_id: string; employee: EmployeeSummary | null;
  phone_number_id: string | null;
  contact_count: number; progress: CampaignProgress;
  created_at: string; updated_at: string;
  scheduled_at?: string | null; timezone?: string | null; calling_window_start?: string | null; calling_window_end?: string | null;
  max_attempts?: number; retry_enabled?: boolean; retry_intervals?: number[];
}
interface Contact {
  id: string; first_name: string | null; last_name: string | null;
  phone_number: string; status: string; attempt_count: number;
  last_called_at: string | null; retry_at?: string | null; callback_at?: string | null; customer_data?: Record<string, string> | null;
}
interface Attempt {
  id: string; attempt_number: number; started_at: string | null; completed_at: string | null;
  status: string; outcome: string | null; duration_seconds: number | null;
  callback_at: string | null; summary: string | null; recording_available: boolean;
}
interface UploadPreview {
  total_rows: number; valid_count: number; invalid_count: number; duplicate_count: number;
  valid_sample: Array<{ phone_number: string; first_name: string; last_name: string; email: string }>;
  invalid_rows: Array<{ row: number; reason: string }>;
  import_token: string;
}
interface PhoneNumber {
  id: string; e164_number: string; status: string;
}

const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-600',
  running: 'bg-emerald-100 text-emerald-700',
  paused: 'bg-amber-100 text-amber-700',
  paused_credits: 'bg-orange-100 text-orange-700', scheduled: 'bg-indigo-100 text-indigo-700', cancelled: 'bg-slate-100 text-slate-600',
  completed: 'bg-blue-100 text-blue-700',
  stopped: 'bg-rose-100 text-rose-700',
  failed: 'bg-rose-100 text-rose-700',
  archived: 'bg-gray-100 text-gray-400',
};
const CONTACT_STATUS_COLORS: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-500',
  in_progress: 'bg-blue-100 text-blue-700',
  called: 'bg-emerald-100 text-emerald-700',
  completed: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-rose-100 text-rose-700',
  skipped: 'bg-gray-100 text-gray-400',
  do_not_call: 'bg-gray-100 text-gray-400',
  retry_scheduled: 'bg-violet-100 text-violet-700', no_answer: 'bg-amber-100 text-amber-700', busy: 'bg-amber-100 text-amber-700',
};

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// ── Bulk Upload Modal ─────────────────────────────────────────────────────────
function BulkUploadModal({ campaignId, onClose, onImported }: {
  campaignId: string; onClose: () => void; onImported: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [preview, setPreview] = useState<UploadPreview | null>(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const handleFile = async (file: File) => {
    const name = file.name.toLowerCase();
    if (!name.endsWith('.csv') && !name.endsWith('.xlsx')) { setError('Only CSV and XLSX files are supported.'); return; }
    if (file.size > 5 * 1024 * 1024) { setError('File exceeds the 5 MB limit.'); return; }
    setError(''); setPreview(null); setUploading(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await backendFetch(`/campaigns/${campaignId}/contacts/upload-preview`, { method: 'POST', body: form, headers: {} });
      const data = await res.json();
      if (!res.ok) { setError(data?.detail || 'Upload failed.'); return; }
      setPreview(data as UploadPreview);
    } catch { setError('Upload failed. Please try again.'); }
    finally { setUploading(false); }
  };

  const confirmImport = async () => {
    if (!preview) return;
    setConfirming(true); setError('');
    try {
      const result = await backendJson<{ imported: number; skipped_duplicates: number }>(
        `/campaigns/${campaignId}/contacts/upload-confirm`,
        { method: 'POST', body: JSON.stringify({ import_token: preview.import_token }) },
      );
      setSuccess(`Imported ${result.imported} contact${result.imported === 1 ? '' : 's'}${result.skipped_duplicates ? `, skipped ${result.skipped_duplicates} duplicate${result.skipped_duplicates === 1 ? '' : 's'}` : ''}.`);
      setPreview(null); onImported();
    } catch (err) { setError(err instanceof Error ? err.message : 'Import failed.'); }
    finally { setConfirming(false); }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg border border-gray-200 flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="font-semibold text-gray-900">Upload contacts</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-400"><X className="w-4 h-4" /></button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {error && <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-sm text-rose-700 flex items-start gap-2"><AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />{error}</div>}
          {success && <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-lg text-sm text-emerald-700 flex items-center gap-2"><CheckCircle className="w-4 h-4" />{success}</div>}
          {!preview && !success && (
            <div
              onDragOver={e => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={e => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files[0]; if (f) void handleFile(f); }}
              onClick={() => fileRef.current?.click()}
              className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors ${dragging ? 'border-blue-400 bg-blue-50' : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'}`}
            >
              <input ref={fileRef} type="file" accept=".csv,.xlsx" className="hidden" onChange={e => { const f = e.target.files?.[0]; if (f) void handleFile(f); }} />
              {uploading
                ? <div className="flex flex-col items-center gap-2 text-gray-500"><div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" /><span className="text-sm">Parsing file…</span></div>
                : <><Upload className="w-8 h-8 text-gray-400 mx-auto mb-3" /><p className="text-sm font-medium text-gray-700">Drag & drop or click to browse</p><p className="text-xs text-gray-400 mt-1">CSV or XLSX · max 5 MB</p></>}
            </div>
          )}
          {preview && (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-3">
                {[{ label: 'Total rows', value: preview.total_rows, color: 'text-gray-700' }, { label: 'Valid', value: preview.valid_count, color: 'text-emerald-600' }, { label: 'Invalid', value: preview.invalid_count, color: 'text-rose-600' }].map(s => (
                  <div key={s.label} className="bg-gray-50 rounded-xl p-3 text-center border border-gray-200">
                    <div className={`text-xl font-bold ${s.color}`}>{s.value}</div>
                    <div className="text-xs text-gray-500">{s.label}</div>
                  </div>
                ))}
              </div>
              {preview.valid_sample.length > 0 && (
                <div className="border border-gray-200 rounded-xl overflow-hidden">
                  <table className="w-full text-xs">
                    <thead className="bg-gray-50 border-b border-gray-200"><tr><th className="text-left px-3 py-2 text-gray-500 font-semibold">Phone</th><th className="text-left px-3 py-2 text-gray-500 font-semibold">Name</th></tr></thead>
                    <tbody className="divide-y divide-gray-100">{preview.valid_sample.map((row, i) => (<tr key={i}><td className="px-3 py-2 font-mono text-gray-700">{row.phone_number}</td><td className="px-3 py-2 text-gray-600">{[row.first_name, row.last_name].filter(Boolean).join(' ') || '—'}</td></tr>))}</tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
        <div className="px-5 py-4 border-t border-gray-200 flex justify-end gap-3">
          {preview ? (
            <>
              <button onClick={() => setPreview(null)} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-xl transition">Back</button>
              <button onClick={() => void confirmImport()} disabled={confirming || preview.valid_count === 0} className="px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-medium rounded-xl transition flex items-center gap-2">
                {confirming && <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
                Import {preview.valid_count} contact{preview.valid_count === 1 ? '' : 's'}
              </button>
            </>
          ) : success ? (
            <button onClick={onClose} className="px-5 py-2 bg-blue-600 text-white text-sm font-medium rounded-xl">Done</button>
          ) : (
            <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-xl transition">Cancel</button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Start Campaign Modal ──────────────────────────────────────────────────────
function StartCampaignModal({ campaignId, onClose, onStarted }: {
  campaignId: string; onClose: () => void; onStarted: (c: CampaignDetail) => void;
}) {
  const [phones, setPhones] = useState<PhoneNumber[]>([]);
  const [selectedPhone, setSelectedPhone] = useState('');
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    backendJson<PhoneNumber[]>('/phone-numbers')
      .then(data => {
        const active = (data || []).filter(p => p.status === 'active');
        setPhones(active);
        if (active.length === 1) setSelectedPhone(active[0].id);
      })
      .catch(() => setError('Could not load phone numbers.'))
      .finally(() => setLoading(false));
  }, []);

  const handleStart = async () => {
    if (!selectedPhone) { setError('Please select a phone number.'); return; }
    setStarting(true); setError('');
    try {
      const result = await backendJson<CampaignDetail>(
        `/campaigns/${campaignId}/start`,
        { method: 'POST', body: JSON.stringify({ phone_number_id: selectedPhone }) },
      );
      onStarted(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start campaign.');
      setStarting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md border border-gray-200">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="font-semibold text-gray-900">Start Campaign</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-400"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-5 space-y-4">
          {error && <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-sm text-rose-700">{error}</div>}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Select phone number</label>
            {loading ? (
              <div className="h-10 bg-gray-100 rounded-xl animate-pulse" />
            ) : phones.length === 0 ? (
              <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
                No active phone numbers. Purchase one from Phone Numbers first.
              </div>
            ) : (
              <div className="space-y-2">
                {phones.map(p => (
                  <label key={p.id} className={`flex items-center gap-3 p-3 border rounded-xl cursor-pointer transition-all ${selectedPhone === p.id ? 'border-violet-500 bg-violet-50' : 'border-gray-200 hover:border-violet-300'}`}>
                    <input type="radio" name="phone" value={p.id} checked={selectedPhone === p.id} onChange={() => setSelectedPhone(p.id)} className="accent-violet-600" />
                    <Phone className="w-4 h-4 text-gray-400" />
                    <span className="font-mono text-sm text-gray-900">{p.e164_number}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="px-5 py-4 border-t border-gray-200 flex justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-xl transition">Cancel</button>
          <button
            onClick={() => void handleStart()}
            disabled={starting || phones.length === 0 || !selectedPhone}
            className="px-5 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white text-sm font-medium rounded-xl transition flex items-center gap-2"
          >
            {starting && <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
            <Play className="w-3.5 h-3.5" /> Start Campaign
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Progress Bar ──────────────────────────────────────────────────────────────
function ProgressBar({ progress }: { progress: CampaignProgress }) {
  const { total, completed, failed, in_progress, skipped } = progress;
  if (total === 0) return null;
  const pct = (n: number) => `${Math.round((n / total) * 100)}%`;
  return (
    <div className="mb-6">
      <div className="flex justify-between text-xs text-gray-500 mb-1">
        <span>{completed} completed · {in_progress} in progress · {failed} failed · {progress.pending} pending</span>
        <span>{Math.round(((completed + skipped) / total) * 100)}%</span>
      </div>
      <div className="h-2 bg-gray-100 rounded-full overflow-hidden flex">
        <div className="bg-emerald-500 h-full transition-all" style={{ width: pct(completed) }} />
        <div className="bg-blue-400 h-full transition-all" style={{ width: pct(in_progress) }} />
        <div className="bg-rose-400 h-full transition-all" style={{ width: pct(failed) }} />
        <div className="bg-gray-300 h-full transition-all" style={{ width: pct(skipped) }} />
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function CampaignDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [campaign, setCampaign] = useState<CampaignDetail | null>(null);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editScheduledAt, setEditScheduledAt] = useState('');
  const [editTimezone, setEditTimezone] = useState('UTC');
  const [editStart, setEditStart] = useState('09:00');
  const [editEnd, setEditEnd] = useState('18:00');
  const [editAttempts, setEditAttempts] = useState(3);
  const [editRetry, setEditRetry] = useState(true);
  const [savingEdit, setSavingEdit] = useState(false);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [showUpload, setShowUpload] = useState(false);
  const [showStart, setShowStart] = useState(false);
  const [acting, setActing] = useState(false);
  const [contactFilter, setContactFilter] = useState('all');
  const [contactSearch, setContactSearch] = useState('');
  const [historyContact, setHistoryContact] = useState<Contact | null>(null);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadCampaign = useCallback(async () => {
    if (!id) return;
    try {
      const camp = await backendJson<CampaignDetail>(`/campaigns/${id}`);
      setCampaign(camp); hydrateEditForm(camp);
    } catch { /* non-fatal refresh */ }
  }, [id]);

  const loadContacts = useCallback(async () => {
    if (!id) return;
    try {
      const data = await backendJson<Contact[]>(`/campaigns/${id}/contacts`);
      setContacts(data || []);
    } catch { /* non-fatal */ }
  }, [id]);

  useEffect(() => {
    if (!id) return;
    const load = async () => {
      try {
        const [camp, data] = await Promise.all([
          backendJson<CampaignDetail>(`/campaigns/${id}`),
          backendJson<Contact[]>(`/campaigns/${id}/contacts`),
        ]);
        setCampaign(camp); hydrateEditForm(camp);
        setContacts(data || []);
      } catch { setError('Failed to load campaign.'); }
      finally { setLoading(false); }
    };
    void load();
  }, [id]);

  // Poll for progress while running
  useEffect(() => {
    if (campaign?.status === 'running' || campaign?.status === 'scheduled' || campaign?.status === 'paused_credits') {
      pollRef.current = setInterval(() => {
        void loadCampaign();
        void loadContacts();
      }, 4000);
    } else {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [campaign?.status, loadCampaign, loadContacts]);

  const doAction = async (action: string) => {
    if (!id) return;
    setActing(true); setActionError('');
    try {
      const result = await backendJson<CampaignDetail>(`/campaigns/${id}/${action}`, { method: 'POST' });
      setCampaign(result);
      await loadContacts();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : `Failed to ${action} campaign.`);
    } finally { setActing(false); }
  };
  const hydrateEditForm = (camp: CampaignDetail) => {
    setEditName(camp.name);
    setEditDescription(camp.description || '');
    setEditScheduledAt(camp.scheduled_at ? new Date(camp.scheduled_at).toISOString().slice(0, 16) : '');
    setEditTimezone(camp.timezone || 'UTC');
    setEditStart(camp.calling_window_start || '09:00');
    setEditEnd(camp.calling_window_end || '18:00');
    setEditAttempts(camp.max_attempts || 3);
    setEditRetry(camp.retry_enabled !== false);
  };
  const saveCampaign = async () => {
    if (!id || !editName.trim()) return;
    setSavingEdit(true); setActionError('');
    try {
      const result = await backendJson<CampaignDetail>(`/campaigns/${id}`, { method: 'PATCH', body: JSON.stringify({
        name: editName.trim(), description: editDescription || null,
        scheduled_at: editScheduledAt ? new Date(editScheduledAt).toISOString() : null,
        timezone: editTimezone, calling_window_start: editStart, calling_window_end: editEnd,
        max_attempts: editAttempts, retry_enabled: editRetry,
      }) });
      setCampaign(result); hydrateEditForm(result); setEditing(false);
    } catch (err) { setActionError(err instanceof Error ? err.message : 'Failed to save campaign.'); }
    finally { setSavingEdit(false); }
  };
  const deleteCampaign = async () => {
    if (!id || !window.confirm('Delete this campaign? It will be archived and removed from the campaign list.')) return;
    setActing(true);
    try { await backendJson(`/campaigns/${id}`, { method: 'DELETE' }); navigate('/campaigns', { replace: true }); }
    catch (err) { setActionError(err instanceof Error ? err.message : 'Failed to delete campaign.'); }
    finally { setActing(false); }
  };

  const openHistory = async (contact: Contact) => {
    if (!id) return;
    setHistoryContact(contact); setAttempts([]); setHistoryError(''); setHistoryLoading(true);
    try {
      setAttempts(await backendJson<Attempt[]>(`/campaigns/${id}/contacts/${contact.id}/attempts`));
    } catch (err) { setHistoryError(err instanceof Error ? err.message : 'Failed to load attempt history.'); }
    finally { setHistoryLoading(false); }
  };

  if (loading) {
    return (
      <div className="flex-1 flex flex-col">
        <div className="h-16 border-b border-gray-200 px-6 flex items-center">
          <div className="h-4 w-48 bg-gray-200 rounded animate-pulse" />
        </div>
        <div className="flex-1 p-6"><div className="h-48 bg-gray-100 rounded-xl animate-pulse" /></div>
      </div>
    );
  }

  if (!campaign) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <h2 className="text-lg font-bold text-gray-900 mb-1">Campaign not found</h2>
          <p className="text-sm text-gray-500 mb-4">{error}</p>
          <button onClick={() => navigate('/campaigns')} className="text-blue-600 text-sm font-medium hover:underline">Go back</button>
        </div>
      </div>
    );
  }

  const cfg = STATUS_COLORS[campaign.status] || STATUS_COLORS.draft;
  const isDraft = campaign.status === 'draft';
  const isRunning = campaign.status === 'running';
  const isPaused = campaign.status === 'paused' || campaign.status === 'paused_credits';
  const isStopped = campaign.status === 'stopped' || campaign.status === 'failed';
  const isCompleted = campaign.status === 'completed';
  const canStart = isDraft && campaign.contact_count > 0 && campaign.employee?.is_ready;
  const canPause = isRunning;
  const canResume = isPaused;
  const canStop = ['scheduled', 'running', 'paused', 'paused_credits'].includes(campaign.status);
  const canRetry = (isStopped || isCompleted) && campaign.progress.failed > 0;
  const visibleContacts = contacts.filter(c => {
    const text = `${c.first_name || ''} ${c.last_name || ''} ${c.phone_number}`.toLowerCase();
    return (!contactSearch || text.includes(contactSearch.toLowerCase())) && (contactFilter === 'all' || c.status === contactFilter || (contactFilter === 'calling' && c.status === 'in_progress'));
  });

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="h-16 border-b border-gray-200 px-6 flex items-center justify-between bg-white">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate('/campaigns')} className="p-1.5 -ml-1.5 rounded-md hover:bg-gray-100 text-gray-500">
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div className="w-8 h-8 rounded-lg bg-violet-100 flex items-center justify-center">
            <Megaphone className="w-4 h-4 text-violet-600" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-gray-900">{campaign.name}</h1>
            <p className="text-xs text-gray-500">{campaign.scheduled_at ? `Scheduled ${new Date(campaign.scheduled_at).toLocaleString()}` : 'Starts now'} · {campaign.timezone || 'UTC'} · Calling {campaign.calling_window_start || 'any time'}–{campaign.calling_window_end || 'any time'}</p>
            <p className="text-xs text-gray-500">{campaign.employee?.name || 'No employee'} · Created {fmtDate(campaign.created_at)}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${cfg}`}>
            {campaign.status.charAt(0).toUpperCase() + campaign.status.slice(1)}
          </span>
          {(isDraft || campaign.status === 'scheduled') && (
            <button onClick={() => setEditing(true)} className="flex items-center gap-1.5 px-3 py-1.5 border border-gray-200 rounded-lg text-xs font-medium text-gray-700 hover:bg-gray-50">
              <Pencil className="w-3.5 h-3.5" /> Edit
            </button>
          )}
          <button onClick={() => void deleteCampaign()} disabled={acting} className="flex items-center gap-1.5 px-3 py-1.5 border border-rose-200 rounded-lg text-xs font-medium text-rose-600 hover:bg-rose-50 disabled:opacity-50">
            <Trash2 className="w-3.5 h-3.5" /> Delete
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {actionError && (
          <div className="mb-4 p-3 bg-rose-50 border border-rose-200 rounded-lg text-sm text-rose-700 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />{actionError}
          </div>
        )}

        {editing && (isDraft || campaign.status === 'scheduled') && (
          <div className="mb-6 p-5 bg-white border border-violet-200 rounded-xl shadow-sm">
            <h2 className="text-sm font-semibold text-gray-900 mb-4">Edit campaign</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <label className="text-xs font-medium text-gray-600">Campaign name<input value={editName} onChange={e => setEditName(e.target.value)} className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm" /></label>
              <label className="text-xs font-medium text-gray-600">Schedule<input type="datetime-local" value={editScheduledAt} onChange={e => setEditScheduledAt(e.target.value)} className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm" /></label>
              <label className="text-xs font-medium text-gray-600 md:col-span-2">Description<textarea value={editDescription} onChange={e => setEditDescription(e.target.value)} rows={2} className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm resize-none" /></label>
              <label className="text-xs font-medium text-gray-600">Timezone<input value={editTimezone} onChange={e => setEditTimezone(e.target.value)} className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm" /></label>
              <label className="text-xs font-medium text-gray-600">Max attempts<input type="number" min={1} max={10} value={editAttempts} onChange={e => setEditAttempts(Number(e.target.value))} className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm" /></label>
              <label className="text-xs font-medium text-gray-600">Calling window<div className="mt-1 flex gap-2"><input type="time" value={editStart} onChange={e => setEditStart(e.target.value)} className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm" /><input type="time" value={editEnd} onChange={e => setEditEnd(e.target.value)} className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm" /></div></label>
              <label className="flex items-center gap-2 text-sm text-gray-700 self-end pb-2"><input type="checkbox" checked={editRetry} onChange={e => setEditRetry(e.target.checked)} /> Retry failed calls</label>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => { hydrateEditForm(campaign); setEditing(false); }} className="px-3 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-lg">Cancel</button>
              <button onClick={() => void saveCampaign()} disabled={savingEdit || !editName.trim()} className="flex items-center gap-1.5 px-4 py-2 bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white text-sm rounded-lg"><Save className="w-3.5 h-3.5" /> {savingEdit ? 'Saving…' : 'Save changes'}</button>
            </div>
          </div>
        )}

        {/* Execution controls */}
        <div className="flex items-center gap-2 mb-6 flex-wrap">
          {canStart && (
            <button
              onClick={() => setShowStart(true)}
              disabled={acting}
              className="flex items-center gap-1.5 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white text-sm font-medium rounded-xl transition"
            >
              <Play className="w-3.5 h-3.5" /> Start Campaign
            </button>
          )}
          {canResume && (
            <button
              onClick={() => void doAction('resume')}
              disabled={acting}
              className="flex items-center gap-1.5 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white text-sm font-medium rounded-xl transition"
            >
              {acting ? <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <Play className="w-3.5 h-3.5" />}
              Resume
            </button>
          )}
          {canPause && (
            <button
              onClick={() => void doAction('pause')}
              disabled={acting}
              className="flex items-center gap-1.5 px-4 py-2 bg-amber-500 hover:bg-amber-600 disabled:opacity-50 text-white text-sm font-medium rounded-xl transition"
            >
              {acting ? <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <Pause className="w-3.5 h-3.5" />}
              Pause
            </button>
          )}
          {canStop && (
            <button
              onClick={() => { if (window.confirm('Cancel pending calls? Active calls may finish.')) void doAction('cancel'); }}
              disabled={acting}
              className="flex items-center gap-1.5 px-4 py-2 bg-rose-600 hover:bg-rose-700 disabled:opacity-50 text-white text-sm font-medium rounded-xl transition"
            >
              {acting ? <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <Square className="w-3.5 h-3.5" />}
              Cancel
            </button>
          )}
          {canRetry && (
            <button
              onClick={() => void doAction('retry')}
              disabled={acting}
              className="flex items-center gap-1.5 px-4 py-2 bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white text-sm font-medium rounded-xl transition"
            >
              {acting ? <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
              Retry Failed
            </button>
          )}
          {isDraft && !campaign.employee?.is_ready && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
              Employee must be published before starting.
            </p>
          )}
          {isDraft && campaign.contact_count === 0 && campaign.employee?.is_ready && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
              Upload contacts before starting.
            </p>
          )}
        </div>

        {/* Progress bar */}
        {campaign.contact_count > 0 && <ProgressBar progress={campaign.progress} />}

        {/* Stats */}
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4 mb-6">
          {[
            { label: 'Total contacts', value: campaign.contact_count, icon: Users, color: 'text-gray-600 bg-gray-50' },
            { label: 'Completed', value: campaign.progress.completed, icon: CheckCircle, color: 'text-emerald-600 bg-emerald-50' },
            { label: 'Pending', value: campaign.progress.pending, icon: Clock, color: 'text-amber-600 bg-amber-50' },
            { label: 'Failed', value: campaign.progress.failed, icon: XCircle, color: 'text-rose-600 bg-rose-50' },
            { label: 'Calling', value: campaign.progress.in_progress, icon: PhoneCall, color: 'text-blue-600 bg-blue-50' },
            { label: 'Retry scheduled', value: campaign.progress.retry_pending || 0, icon: RotateCcw, color: 'text-violet-600 bg-violet-50' },
          ].map(stat => (
            <div key={stat.label} className="bg-white border border-gray-200 rounded-xl p-4 flex items-center gap-3">
              <div className={`w-9 h-9 ${stat.color} rounded-lg flex items-center justify-center flex-shrink-0`}>
                <stat.icon className="w-4 h-4" />
              </div>
              <div>
                <div className="text-xl font-bold text-gray-900">{stat.value}</div>
                <div className="text-xs text-gray-500">{stat.label}</div>
              </div>
            </div>
          ))}
        </div>

        {/* Contacts section */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">Contacts ({contacts.length})</h2>
            {isDraft && (
              <button
                onClick={() => setShowUpload(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium rounded-lg transition"
              >
                <Upload className="w-3.5 h-3.5" /> Upload document
              </button>
            )}
          </div>
          {contacts.length > 0 && <div className="mb-3 flex flex-wrap gap-2"><input value={contactSearch} onChange={e => setContactSearch(e.target.value)} placeholder="Search name or phone" className="rounded-lg border border-gray-200 px-3 py-2 text-sm" /><select value={contactFilter} onChange={e => setContactFilter(e.target.value)} className="rounded-lg border border-gray-200 px-3 py-2 text-sm"><option value="all">All statuses</option><option value="pending">Pending</option><option value="calling">Calling</option><option value="completed">Completed</option><option value="retry_scheduled">Retry scheduled</option><option value="no_answer">No answer</option><option value="busy">Busy</option><option value="failed">Failed</option><option value="do_not_call">Do not call</option><option value="cancelled">Cancelled</option></select></div>}
          {contacts.length === 0 ? (
            <div className="bg-white border border-gray-200 rounded-xl p-10 text-center">
              <div className="w-10 h-10 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-3">
                <PhoneCall className="w-5 h-5 text-gray-400" />
              </div>
              <h3 className="text-sm font-medium text-gray-900 mb-1">No contacts yet</h3>
              <p className="text-xs text-gray-500 mb-4">Upload a CSV or XLSX file to add contacts to this campaign.</p>
              {isDraft && (
                <button onClick={() => setShowUpload(true)} className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition">
                  <Upload className="w-3.5 h-3.5" /> Upload contacts
                </button>
              )}
            </div>
          ) : (
            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
              <table className="w-full">
                <thead className="border-b border-gray-200 bg-gray-50">
                  <tr>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3">Contact</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3">Phone</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3">Status</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3 hidden sm:table-cell">Attempts</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3 hidden lg:table-cell">Next / Last</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-3 hidden xl:table-cell">Uploaded fields</th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {visibleContacts.map(c => {
                    const sc = CONTACT_STATUS_COLORS[c.status] || 'bg-gray-100 text-gray-500';
                    return (
                      <tr key={c.id} className="hover:bg-gray-50 transition">
                        <td className="px-4 py-3 text-sm font-medium text-gray-900">{[c.first_name, c.last_name].filter(Boolean).join(' ') || '—'}</td>
                        <td className="px-4 py-3 font-mono text-xs text-gray-600">{c.phone_number}</td>
                        <td className="px-4 py-3"><span className={`text-xs px-2 py-0.5 rounded-full ${sc}`}>{c.status.replace(/_/g, ' ')}</span></td>
                        <td className="px-4 py-3 text-sm text-gray-500 hidden sm:table-cell">{c.attempt_count}{campaign.max_attempts ? ` / ${campaign.max_attempts}` : ''}</td>
                        <td className="px-4 py-3 text-xs text-gray-500 hidden lg:table-cell">{c.callback_at ? `Callback ${new Date(c.callback_at).toLocaleString()}` : c.retry_at ? `Retry ${new Date(c.retry_at).toLocaleString()}` : c.last_called_at ? fmtDate(c.last_called_at) : '—'}</td>
                        <td className="px-4 py-3 text-xs text-gray-500 hidden xl:table-cell max-w-xs">{Object.entries(c.customer_data || {}).map(([key, value]) => `${key}: ${value}`).join(' · ') || '—'}</td>
                        <td className="px-4 py-3 text-right"><button onClick={() => void openHistory(c)} className="inline-flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-800"><History className="w-3.5 h-3.5" /> View History</button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Details */}
        <section className="mt-6">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">Details</h2>
          <div className="bg-white border border-gray-200 rounded-xl divide-y divide-gray-100">
            {[
              ['Employee', campaign.employee?.name || '—'],
              ['Status', campaign.status],
              ['Description', campaign.description || '—'],
              ['Created', fmtDate(campaign.created_at)],
              ['Updated', fmtDate(campaign.updated_at)],
            ].map(([label, value]) => (
              <div key={label} className="px-4 py-3 flex items-start gap-3">
                <span className="text-xs text-gray-500 w-24 flex-shrink-0">{label}</span>
                <span className="text-sm text-gray-900">{value}</span>
              </div>
            ))}
          </div>
        </section>
      </div>

      {showUpload && (
        <BulkUploadModal
          campaignId={campaign.id}
          onClose={() => setShowUpload(false)}
          onImported={() => { void loadContacts(); void loadCampaign(); }}
        />
      )}
      {showStart && (
        <StartCampaignModal
          campaignId={campaign.id}
          onClose={() => setShowStart(false)}
          onStarted={c => { setCampaign(c); setShowStart(false); void loadContacts(); }}
        />
      )}
      {historyContact && (
        <div className="fixed inset-0 z-50 bg-gray-900/30 flex justify-end" onClick={() => setHistoryContact(null)}>
          <aside className="w-full max-w-xl bg-[#fafafa] h-full overflow-y-auto shadow-xl" onClick={e => e.stopPropagation()}>
            <div className="p-5 border-b border-gray-200 bg-white flex items-start justify-between">
              <div><h2 className="text-lg font-bold text-gray-900">Attempt history</h2><p className="text-sm text-gray-500 mt-1">{[historyContact.first_name, historyContact.last_name].filter(Boolean).join(' ') || 'Unnamed contact'} · {historyContact.phone_number}</p></div>
              <button onClick={() => setHistoryContact(null)} className="p-2 rounded-lg hover:bg-gray-100 text-gray-500"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-5">
              <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4 flex justify-between text-sm"><span>Current status <strong className="ml-1 text-gray-900">{historyContact.status.replace(/_/g, ' ')}</strong></span><span>Attempts <strong className="ml-1 text-gray-900">{historyContact.attempt_count}</strong></span></div>
              {historyLoading && <div className="text-sm text-gray-500 py-8 text-center">Loading attempt history…</div>}
              {historyError && <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg p-3">{historyError}</div>}
              {!historyLoading && !historyError && attempts.length === 0 && <div className="text-sm text-gray-500 bg-white border border-gray-200 rounded-xl p-8 text-center">No call attempts yet.</div>}
              <div className="space-y-3">{attempts.map((a, index) => <div key={a.id} className={`bg-white border rounded-xl p-4 ${index === 0 ? 'border-blue-300 ring-1 ring-blue-100' : 'border-gray-200'}`}>
                <div className="flex justify-between gap-3"><div className="font-semibold text-gray-900">Attempt {a.attempt_number} {index === 0 && <span className="ml-2 text-xs font-medium text-blue-700">Latest</span>}</div><span className={`text-xs px-2 py-1 rounded-full ${CONTACT_STATUS_COLORS[a.status] || 'bg-gray-100 text-gray-600'}`}>{a.status.replace(/_/g, ' ')}</span></div>
                <div className="text-xs text-gray-500 mt-2">{a.started_at ? new Date(a.started_at).toLocaleString() : 'Date unavailable'} · {a.duration_seconds != null ? `${a.duration_seconds}s` : 'Duration unavailable'}</div>
                {a.outcome && <div className="text-sm text-gray-700 mt-2">Outcome: {a.outcome}</div>}{a.callback_at && <div className="text-xs text-amber-700 mt-1">Callback: {new Date(a.callback_at).toLocaleString()}</div>}{a.summary && <p className="text-sm text-gray-600 mt-2">{a.summary}</p>}{a.recording_available && <div className="text-xs text-gray-500 mt-2">Recording available through the existing secure recording flow.</div>}
              </div>)}</div>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

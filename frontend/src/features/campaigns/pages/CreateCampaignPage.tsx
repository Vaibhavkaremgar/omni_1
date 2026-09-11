import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, CheckCircle, Globe, Megaphone, AlertTriangle, ExternalLink } from 'lucide-react';
import { backendJson } from '../../../services/backend/api';

interface Employee {
  id: string;
  name: string;
  purpose: string;
  language: string;
  status: string;
  provider_agent_id: string | null;
}

export default function CreateCampaignPage() {
  const navigate = useNavigate();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [form, setForm] = useState({ name: '', description: '', employee_id: '' });

  useEffect(() => {
    backendJson<Employee[]>('/employees')
      .then(data => setEmployees(data || []))
      .catch(() => setEmployees([]));
  }, []);

  const selected = employees.find(e => e.id === form.employee_id) ?? null;
  const isReady = selected?.status === 'published' && Boolean(selected?.provider_agent_id);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) { setError('Campaign name is required.'); return; }
    if (!form.employee_id) { setError('Please select an AI employee.'); return; }
    if (!isReady) { setError('The selected employee must be published before it can be used for calls.'); return; }
    setLoading(true);
    setError('');
    try {
      const data = await backendJson<{ id: string }>('/campaigns', {
        method: 'POST',
        body: JSON.stringify({ name: form.name.trim(), description: form.description || null, employee_id: form.employee_id }),
      });
      navigate(`/campaigns/${data.id}`, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.');
      setLoading(false);
    }
  };

  const publishedEmployees = employees.filter(e => e.status === 'published');
  const draftEmployees = employees.filter(e => e.status !== 'published');

  return (
    <div className="flex-1 flex flex-col">
      <div className="flex items-center h-16 px-6 border-b border-gray-200 bg-white gap-3">
        <button onClick={() => navigate('/campaigns')} className="p-1 -ml-1 rounded-md hover:bg-gray-100 text-gray-500">
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div className="w-8 h-8 rounded-lg bg-violet-100 flex items-center justify-center">
          <Megaphone className="w-4 h-4 text-violet-600" />
        </div>
        <div>
          <h1 className="text-lg font-bold text-gray-900">New Campaign</h1>
          <p className="text-xs text-gray-500">Set up a bulk outbound campaign</p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        <form onSubmit={handleCreate} className="max-w-2xl mx-auto space-y-6">
          {error && (
            <div className="p-3 bg-rose-50 border border-rose-200 rounded-xl text-sm text-rose-700">{error}</div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Campaign name *</label>
            <input
              type="text"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              placeholder="e.g. Q4 Webinar Follow-up"
              className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-500/30 focus:border-violet-400 transition"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <textarea
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              placeholder="What is this campaign for?"
              className="w-full px-3 py-2.5 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-violet-500/30 focus:border-violet-400 transition resize-none h-24"
            />
          </div>

          {/* Employee selection */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">AI Employee *</label>
            {employees.length === 0 ? (
              <div className="p-4 bg-amber-50 border border-amber-200 rounded-xl text-sm text-amber-800">
                No employees found.{' '}
                <button type="button" onClick={() => navigate('/employees/new')} className="font-semibold underline">
                  Create one first
                </button>
              </div>
            ) : (
              <div className="space-y-2">
                {publishedEmployees.length > 0 && (
                  <>
                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Ready to use</p>
                    {publishedEmployees.map(emp => (
                      <label
                        key={emp.id}
                        className={`flex items-start gap-3 p-3 border rounded-xl cursor-pointer transition-all ${
                          form.employee_id === emp.id
                            ? 'border-violet-500 bg-violet-50'
                            : 'border-gray-200 hover:border-violet-300 hover:bg-violet-50/40'
                        }`}
                      >
                        <input
                          type="radio"
                          name="employee_id"
                          value={emp.id}
                          checked={form.employee_id === emp.id}
                          onChange={() => setForm(f => ({ ...f, employee_id: emp.id }))}
                          className="mt-0.5 accent-violet-600"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-semibold text-gray-900">{emp.name}</span>
                            <span className="inline-flex items-center gap-1 text-xs text-emerald-700 bg-emerald-100 rounded-full px-2 py-0.5">
                              <CheckCircle className="w-3 h-3" /> Published
                            </span>
                          </div>
                          <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{emp.purpose}</p>
                          <span className="inline-flex items-center gap-1 text-xs text-gray-500 mt-1">
                            <Globe className="w-3 h-3" /> {emp.language}
                          </span>
                        </div>
                      </label>
                    ))}
                  </>
                )}

                {draftEmployees.length > 0 && (
                  <>
                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mt-3">Not yet published</p>
                    {draftEmployees.map(emp => (
                      <div
                        key={emp.id}
                        className="flex items-start gap-3 p-3 border border-gray-200 rounded-xl opacity-60"
                      >
                        <div className="mt-0.5 w-4 h-4 rounded-full border-2 border-gray-300" />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-gray-700">{emp.name}</span>
                            <span className="text-xs text-amber-700 bg-amber-100 rounded-full px-2 py-0.5">Draft</span>
                          </div>
                          <p className="text-xs text-gray-400 mt-0.5 line-clamp-1">{emp.purpose}</p>
                          <button
                            type="button"
                            onClick={() => navigate(`/employees/${emp.id}`)}
                            className="inline-flex items-center gap-1 text-xs text-violet-600 hover:underline mt-1"
                          >
                            <ExternalLink className="w-3 h-3" /> Publish this employee first
                          </button>
                        </div>
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>

          {/* Selected employee summary */}
          {selected && (
            <div className={`p-4 rounded-xl border ${isReady ? 'bg-emerald-50 border-emerald-200' : 'bg-amber-50 border-amber-200'}`}>
              {isReady ? (
                <div className="flex items-start gap-2">
                  <CheckCircle className="w-4 h-4 text-emerald-600 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm font-semibold text-emerald-900">
                      Calls will be handled by <span className="font-bold">{selected.name}</span>
                    </p>
                    <p className="text-xs text-emerald-700 mt-0.5">
                      This employee is published and ready. Every call in this campaign will follow its purpose, tasks, and conversation rules.
                    </p>
                  </div>
                </div>
              ) : (
                <div className="flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm font-semibold text-amber-900">Employee not ready</p>
                    <p className="text-xs text-amber-700 mt-0.5">
                      <strong>{selected.name}</strong> must be published before it can handle calls.{' '}
                      <button type="button" onClick={() => navigate(`/employees/${selected.id}`)} className="underline font-semibold">
                        Publish it now
                      </button>
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => navigate('/campaigns')}
              className="px-4 py-2.5 text-sm text-gray-600 hover:bg-gray-100 rounded-xl transition"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || !isReady}
              className="px-6 py-2.5 bg-violet-600 hover:bg-violet-700 disabled:opacity-50 text-white font-medium rounded-xl transition text-sm flex items-center gap-2"
            >
              {loading && <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
              Create Campaign
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

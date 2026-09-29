import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Check, CheckCircle2, FileSpreadsheet, Megaphone, Phone, Search, ShieldCheck, Sparkles, Upload } from 'lucide-react';
import { backendFetch, backendJson } from '../../../services/backend/api';

type Employee = {
  id: string;
  name: string;
  purpose: string;
  language: string;
  status: string;
  provider_status?: string | null;
  configuration?: Record<string, unknown> | null;
};
type PhoneNumber = { id: string; e164_number: string; status: string; provider_phone_number_id?: string | null };
type Preview = { total_rows: number; valid_count: number; invalid_count: number; duplicate_count: number; import_token: string };

function businessNameFor(employee: Employee) {
  const value = employee.configuration?.business_name;
  return typeof value === 'string' && value.trim() ? value.trim() : 'Business name not set';
}

function Step({ number, title, text }: { number: string; title: string; text: string }) {
  return (
    <div className="mb-4 flex items-center gap-3">
      <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-violet-100 text-sm font-bold text-violet-700">{number}</span>
      <div><h2 className="font-semibold text-slate-900">{title}</h2><p className="text-xs text-slate-500">{text}</p></div>
    </div>
  );
}

export default function CreateCampaignPage() {
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [phones, setPhones] = useState<PhoneNumber[]>([]);
  const [employeeId, setEmployeeId] = useState('');
  const [employeeSearch, setEmployeeSearch] = useState('');
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [campaignId, setCampaignId] = useState('');
  const [name, setName] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    void Promise.all([backendJson<Employee[]>('/employees'), backendJson<PhoneNumber[]>('/phone-numbers')])
      .then(([staff, numbers]) => {
        setEmployees(staff);
        const active = numbers.filter(number => number.status === 'active' && number.provider_phone_number_id);
        setPhones(active);
        if (active.length === 1) setPhoneNumberId(active[0].id);
      })
      .catch(() => setError('Unable to load employees or phone numbers.'));
  }, []);

  const available = employees.filter(item => item.status === 'published' && item.provider_status !== 'failed');
  const query = employeeSearch.trim().toLocaleLowerCase();
  const filteredEmployees = available.filter(item => !query || `${businessNameFor(item)} ${item.name}`.toLocaleLowerCase().includes(query));
  const employee = available.find(item => item.id === employeeId);

  const selectEmployee = (id: string) => {
    setEmployeeId(id);
    setCampaignId('');
    setPreview(null);
    setFile(null);
  };

  const upload = async (next: File) => {
    if (!employee) return setError('Select a published AI Employee before uploading contacts.');
    if (!/\.(csv|xlsx|xls)$/i.test(next.name)) return setError('Only CSV, XLS, and XLSX files are supported.');
    setBusy(true);
    setError('');
    setFile(next);
    try {
      let id = campaignId;
      if (!id) {
        const campaign = await backendJson<{ id: string }>('/campaigns', {
          method: 'POST',
          body: JSON.stringify({ employee_id: employee.id, name: name.trim() || 'Untitled campaign' }),
        });
        id = campaign.id;
        setCampaignId(id);
      }
      const form = new FormData();
      form.append('file', next);
      const response = await backendFetch(`/campaigns/${id}/contacts/upload-preview`, { method: 'POST', body: form, headers: {} });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.detail || 'Unable to read file.');
      setPreview(data);
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : 'Unable to read file.');
    } finally {
      setBusy(false);
    }
  };

  const start = async () => {
    if (!campaignId || !preview || !phoneNumberId) return setError('Upload contacts and select the calling phone number first.');
    setBusy(true);
    setError('');
    try {
      await backendJson(`/campaigns/${campaignId}/contacts/upload-confirm`, {
        method: 'POST',
        body: JSON.stringify({ import_token: preview.import_token }),
      });
      await backendJson(`/campaigns/${campaignId}`, {
        method: 'PATCH',
        body: JSON.stringify({ name: name.trim() || 'Untitled campaign' }),
      });
      await backendJson(`/campaigns/${campaignId}/start`, {
        method: 'POST',
        body: JSON.stringify({ phone_number_id: phoneNumberId }),
      });
      navigate(`/campaigns/${campaignId}`, { replace: true });
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : 'Campaign could not be started.');
    } finally {
      setBusy(false);
    }
  };

  const card = 'rounded-2xl border border-slate-200 bg-white p-5 shadow-sm';

  return (
    <div className="min-h-full flex-1 bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-3 px-4 sm:px-6">
          <button onClick={() => navigate('/campaigns')} className="rounded-xl p-2 text-slate-500 hover:bg-slate-100"><ArrowLeft className="h-5 w-5" /></button>
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-100"><Megaphone className="h-4 w-4 text-violet-600" /></span>
          <div><h1 className="font-bold text-slate-900">New bulk campaign</h1><p className="text-xs text-slate-500">Build, review, and launch your outreach.</p></div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl p-4 sm:p-6">
        <section className="mb-6 rounded-3xl bg-gradient-to-br from-violet-700 via-violet-600 to-indigo-600 p-6 text-white shadow-xl shadow-violet-200">
          <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
            <div>
              <p className="mb-2 flex items-center gap-2 text-sm font-semibold text-violet-100"><Sparkles className="h-4 w-4" /> Campaign builder</p>
              <h2 className="text-2xl font-bold">Turn your contact list into meaningful conversations.</h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-violet-100">Choose a published employee, validate your spreadsheet, and launch from a verified number.</p>
            </div>
            <div className="flex gap-3 text-xs font-semibold text-violet-100">
              <span className={employee ? 'text-white' : ''}>01 Employee</span>
              <span className={preview ? 'text-white' : ''}>02 Contacts</span>
              <span className={phoneNumberId ? 'text-white' : ''}>03 Launch</span>
            </div>
          </div>
        </section>

        {error && <div className="mb-5 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}

        <div className="grid gap-6 lg:grid-cols-[1fr_280px]">
          <div className="space-y-5">
            <section className={card}>
              <Step number="1" title="Campaign details" text="Give this outreach a recognizable name." />
              <label className="block text-sm font-medium text-slate-700">
                Campaign name
                <input className="mt-2 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm outline-none placeholder:text-slate-400 focus:border-violet-500 focus:bg-white focus:ring-4 focus:ring-violet-100" value={name} onChange={event => setName(event.target.value)} placeholder="e.g. GreenNest site visit follow-up" />
              </label>
            </section>

            <section className={card}>
              <Step number="2" title="Choose an AI employee" text="Search by business or employee name, then select one." />
              {available.length ? (
                <>
                  <label className="relative mb-4 block">
                    <span className="sr-only">Search employees</span>
                    <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                    <input type="search" value={employeeSearch} onChange={event => setEmployeeSearch(event.target.value)} placeholder="Search business or employee name" className="w-full rounded-xl border border-slate-200 bg-slate-50 py-3 pl-10 pr-4 text-sm outline-none placeholder:text-slate-400 focus:border-violet-500 focus:bg-white focus:ring-4 focus:ring-violet-100" />
                  </label>
                  {filteredEmployees.length ? (
                    <div className="grid gap-3 sm:grid-cols-2">
                      {filteredEmployees.map(item => {
                        const selected = employeeId === item.id;
                        return (
                          <label key={item.id} className={`group cursor-pointer rounded-2xl border p-4 transition ${selected ? 'border-violet-500 bg-violet-50 ring-4 ring-violet-100' : 'border-slate-200 bg-white hover:border-violet-300 hover:bg-violet-50/40'}`}>
                            <input className="sr-only" type="radio" name="employee" checked={selected} onChange={() => selectEmployee(item.id)} />
                            <div className="flex items-center gap-3">
                              <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-sm font-bold ${selected ? 'bg-violet-600 text-white' : 'bg-violet-100 text-violet-700'}`}>{item.name.slice(0, 1).toUpperCase()}</span>
                              <div className="min-w-0 flex-1">
                                <p className="truncate text-xs font-semibold uppercase tracking-wide text-slate-400">{businessNameFor(item)}</p>
                                <p className="mt-1 truncate text-sm font-bold text-slate-900">{item.name}</p>
                              </div>
                              <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border ${selected ? 'border-violet-600 bg-violet-600 text-white' : 'border-slate-300 text-transparent group-hover:border-violet-400'}`}><Check className="h-3.5 w-3.5" /></span>
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  ) : <p className="rounded-xl bg-slate-50 p-4 text-center text-sm text-slate-500">No published employees match “{employeeSearch.trim()}”.</p>}
                </>
              ) : <p className="rounded-xl bg-amber-50 p-4 text-sm text-amber-800">No published employees are available yet.</p>}
            </section>

            <section className={card}>
              <Step number="3" title="Upload your contacts" text="CSV, XLS, or XLSX — validated before calling begins." />
              <button type="button" disabled={!employee || busy} onClick={() => fileRef.current?.click()} className={`flex w-full flex-col items-center rounded-2xl border-2 border-dashed px-6 py-10 text-center transition ${employee ? 'border-violet-200 bg-violet-50/50 hover:border-violet-400 hover:bg-violet-50' : 'cursor-not-allowed border-slate-200 bg-slate-50 opacity-60'}`}>
                <input ref={fileRef} type="file" className="hidden" accept=".csv,.xls,.xlsx" onChange={event => { const selectedFile = event.target.files?.[0]; if (selectedFile) void upload(selectedFile); }} />
                {busy ? <span className="h-7 w-7 animate-spin rounded-full border-2 border-violet-600 border-t-transparent" /> : <Upload className="mb-3 h-7 w-7 text-violet-600" />}
                <b className="text-sm text-slate-800">{busy ? 'Reading your file…' : 'Choose a contact file'}</b>
                <span className="mt-1 text-xs text-slate-500">CSV, XLS, XLSX · max 5 MB</span>
              </button>
              {file && <div className="mt-4 flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3"><FileSpreadsheet className="h-5 w-5 text-emerald-600" /><span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-800">{file.name}</span><CheckCircle2 className="h-5 w-5 text-emerald-500" /></div>}
              {preview && (
                <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {[['Rows', preview.total_rows, 'text-slate-800'], ['Ready', preview.valid_count, 'text-emerald-600'], ['Invalid', preview.invalid_count, 'text-rose-600'], ['Duplicates', preview.duplicate_count, 'text-amber-600']].map(([label, value, color]) => (
                    <div key={String(label)} className="rounded-xl border border-slate-200 p-3"><b className={`block text-xl ${color}`}>{value}</b><span className="text-xs text-slate-500">{label}</span></div>
                  ))}
                </div>
              )}
            </section>

            {preview && (
              <section className={card}>
                <Step number="4" title="Select a calling number" text="This is the verified number contacts will see." />
                {phones.length ? (
                  <div className="grid gap-3 sm:grid-cols-2">
                    {phones.map(phone => (
                      <label key={phone.id} className={`cursor-pointer rounded-xl border p-4 ${phoneNumberId === phone.id ? 'border-violet-500 bg-violet-50 ring-4 ring-violet-100' : 'border-slate-200 hover:border-violet-300'}`}>
                        <input className="sr-only" type="radio" name="campaign-phone" checked={phoneNumberId === phone.id} onChange={() => setPhoneNumberId(phone.id)} />
                        <span className="flex items-center gap-3"><Phone className="h-4 w-4 text-violet-600" /><span className="font-mono text-sm font-medium text-slate-800">{phone.e164_number}</span></span>
                      </label>
                    ))}
                  </div>
                ) : <p className="rounded-xl bg-amber-50 p-4 text-sm text-amber-800">No active outbound numbers are available.</p>}
              </section>
            )}
          </div>

          <aside className="h-fit rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:sticky lg:top-6">
            <div className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-emerald-600" /><h2 className="font-semibold text-slate-900">Launch checklist</h2></div>
            <div className="mt-5 space-y-4 text-sm">
              {[{ ok: Boolean(name.trim()), label: 'Campaign has a name' }, { ok: Boolean(employee), label: 'Published employee selected' }, { ok: Boolean(preview?.valid_count), label: 'Contacts validated' }, { ok: Boolean(phoneNumberId), label: 'Calling number selected' }].map(item => (
                <div key={item.label} className="flex gap-3"><span className={`mt-0.5 flex h-5 w-5 items-center justify-center rounded-full ${item.ok ? 'bg-emerald-100 text-emerald-600' : 'bg-slate-100 text-slate-400'}`}>{item.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : '•'}</span><span className={item.ok ? 'text-slate-700' : 'text-slate-400'}>{item.label}</span></div>
              ))}
            </div>
            <div className="mt-6 border-t border-slate-100 pt-5">
              <button onClick={() => void start()} disabled={busy || !preview || !phoneNumberId || preview.valid_count === 0} className="flex w-full items-center justify-center gap-2 rounded-xl bg-violet-600 px-4 py-3 text-sm font-bold text-white shadow-lg shadow-violet-200 transition hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-40"><Megaphone className="h-4 w-4" />{busy ? 'Preparing campaign…' : 'Launch campaign'}</button>
              <p className="mt-3 text-center text-xs leading-5 text-slate-500">Your contacts are validated before calling starts.</p>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}

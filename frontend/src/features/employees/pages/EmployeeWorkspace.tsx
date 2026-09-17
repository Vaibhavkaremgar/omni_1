import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Loader2, MessageCircle, PhoneCall, Save, Sparkles } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { backendJson } from '../../../services/backend/api';
import WorkspaceExtensions from '../components/WorkspaceExtensions';

const languages = ['English', 'Hindi', 'Telugu', 'Tamil', 'Kannada', 'Malayalam', 'Marathi', 'Bengali', 'Gujarati', 'Punjabi', 'Odia', 'Assamese'];
const sectionNames = ['Identity & Purpose', 'Greeting & Intro', 'Qualification', 'Handling Objections', 'Call to Action', 'Closing'];
type Step = { key: string; title: string; content: string };
type Variable = { key: string; label: string; description: string; type: string; required: boolean };
export type Config = { [key: string]: unknown; opening?: string; final_prompt?: string; call_script?: Record<string, string>; custom_sections?: Step[]; conversation_variables?: Variable[] };
type Voice = { id: string; name: string; tier: string; gender: string; languages?: string[]; is_cloned?: boolean };
type VoiceRecommendation = { id: string; reason: string };
type Employee = { id: string; name: string; purpose: string; language: string; call_type: string; status: string; configuration?: Config | null };
type Phone = { id: string; e164_number: string; status: string; label?: string | null };
type Session = { messages: Array<{ role: string; content: string }>; current_question: string | null; is_complete: boolean; extracted_configuration: Config };

export default function EmployeeWorkspace() {
  const navigate = useNavigate();
  const { id = '' } = useParams();
  const [employee, setEmployee] = useState<Employee | null>(null);
  const [config, setConfig] = useState<Config>({});
  const [name, setName] = useState('');
  const [requirement, setRequirement] = useState('');
  const [language, setLanguage] = useState('Telugu');
  const [callType, setCallType] = useState<'inbound' | 'outbound'>('inbound');
  const [voiceId, setVoiceId] = useState('');
  const [voices, setVoices] = useState<Voice[]>([]);
  const [phones, setPhones] = useState<Phone[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [answer, setAnswer] = useState('');
  const [destination, setDestination] = useState('');
  const [fromPhone, setFromPhone] = useState('');
  const [testOpen, setTestOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [calling, setCalling] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = async () => {
    try {
      const [e, ps] = await Promise.all([backendJson<Employee>(`/employees/${id}`), backendJson<Phone[]>('/phone-numbers')]);
      const c = e.configuration ?? {};
      setEmployee(e); setConfig(c); setName(e.name); setRequirement(String(c.original_requirement ?? c.business_description ?? e.purpose)); setLanguage(String(c.language ?? e.language)); setCallType((c.call_type ?? e.call_type) as 'inbound' | 'outbound');
      setVoiceId(String((c.voice as { id?: string } | undefined)?.id ?? ''));
      const active = ps.filter(p => p.status === 'active'); setPhones(active); if (active[0]) setFromPhone(active[0].id);
    } catch { setError('Unable to load this employee.'); }
  };
  useEffect(() => { const timer = window.setTimeout(() => { if (id) void load(); void backendJson<Voice[]>('/employees/voice-catalog').then(items => { setVoices(items); const cloned = items.filter(v => v.is_cloned || v.tier === 'cloned' || v.tier === 'custom'); if (cloned.length) setNotice(`My Cloned Voices: ${cloned.map(v => `${v.name} · ${v.gender} · ${v.languages?.join(', ') || 'Language not specified'} · Ready`).join(' | ')}`); }).catch(() => setError('Voice catalog could not be loaded.')); }, 0); return () => window.clearTimeout(timer); }, [id]);
  useEffect(() => { if (requirement.trim().length < 2) return; const timer = window.setTimeout(() => { const params = new URLSearchParams({ language, requirement: requirement.trim() }); void backendJson<{ recommended_voices: VoiceRecommendation[] }>(`/employees/voice-recommendations?${params}`).then(result => { const names = result.recommended_voices.map(item => voices.find(voice => voice.id === item.id)?.name).filter(Boolean); if (names.length) setNotice(`Voice Suggestions: Recommended for your employee: ${names.join(' · ')}`); }).catch(() => undefined); }, 500); return () => window.clearTimeout(timer); }, [language, requirement, voices]);

  const languageVoices = useMemo(() => voices.filter(v => !v.languages?.length || v.languages.includes(language) || v.is_cloned || v.tier === 'cloned' || v.tier === 'custom'), [voices, language]);
  const selectedVoice = voices.find(v => v.id === voiceId);
  const script = config.call_script ?? {};
  const businessName = String(config.business_name ?? '');
  useEffect(() => { if (testOpen && !destination) setDestination('+91'); }, [testOpen, destination]);

  const save = async (publish = false) => {
    setBusy(true); setError(''); setNotice('');
    try {
      const next = { ...config, name: name.trim(), purpose: requirement, original_requirement: requirement, language, call_type: callType, ...(voiceId ? { voice: { id: voiceId, name: selectedVoice?.name, tier: selectedVoice?.tier, gender: selectedVoice?.gender } } : {}) };
      const updated = await backendJson<Employee>(`/employees/${id}`, { method: 'PATCH', body: JSON.stringify({ name: name.trim(), language, configuration: next }) });
      setEmployee(updated); setConfig(updated.configuration ?? next);
      if (publish) { const published = await backendJson<Employee>(`/employees/${id}/publish`, { method: 'POST' }); setEmployee(published); setConfig(published.configuration ?? next); setNotice('Employee published successfully.'); } else setNotice('Changes saved.');
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to save employee.'); } finally { setBusy(false); }
  };
  const startChat = async () => { setBusy(true); try { setSession(await backendJson<Session>(`/employees/${id}/interview/start`, { method: 'POST', body: '{}' })); } catch { setError('Shabdha could not be started.'); } finally { setBusy(false); } };
  const sendAnswer = async () => { if (!session?.current_question || !answer.trim()) return; setBusy(true); try { const next = await backendJson<Session>(`/employees/${id}/interview/answer`, { method: 'POST', body: JSON.stringify({ question: session.current_question, answer }) }); setSession(next); setConfig(current => ({ ...current, ...next.extracted_configuration, custom_sections: next.extracted_configuration.custom_sections ?? current.custom_sections, conversation_variables: next.extracted_configuration.conversation_variables ?? current.conversation_variables })); setAnswer(''); } catch { setError('Shabdha could not save that answer.'); } finally { setBusy(false); } };
  const testCall = async () => { const number = destination.replace(/[ ()-]/g, ''); if (!fromPhone || !/^\+[1-9]\d{6,14}$/.test(number)) { setError('Enter a valid E.164 destination number and select a calling number.'); return; } setCalling(true); try { const call = await backendJson<{ status: string }>('/calls/instant', { method: 'POST', body: JSON.stringify({ employee_id: id, phone_number_id: fromPhone, destination_phone_number: number, customer_name: name, context: 'Test call from the employee workspace.' }) }); setNotice(`Test call ${call.status}.`); setTestOpen(false); setDestination(''); } catch (e) { setError(e instanceof Error ? e.message : 'Unable to start the test call.'); } finally { setCalling(false); } };

  if (!employee) return <main className="flex-1 bg-slate-50 p-10"><div className="mx-auto max-w-7xl animate-pulse rounded-2xl bg-white p-10 text-slate-400">Loading employee workspace...</div></main>;
  return <main className="flex-1 overflow-y-auto bg-slate-50"><div className="mx-auto max-w-7xl p-4 sm:p-6 lg:p-10">
    <button onClick={() => navigate('/employees')} className="mb-5 flex items-center gap-2 text-sm text-slate-500"><ArrowLeft size={16} /> My Employees</button>
    <header className="mb-7 flex flex-wrap items-start justify-between gap-4"><div><p className="text-sm font-semibold text-blue-600">Employee workspace</p><h1 className="mt-1 text-3xl font-bold text-slate-950">{name}</h1><p className="mt-1 text-sm text-slate-500">{String(config.role ?? 'AI employee')} {businessName && `· ${businessName}`} <span className="mx-1">·</span><span className={employee.status === 'published' ? 'text-emerald-700' : 'text-amber-700'}>● {employee.status === 'published' ? 'Published' : 'Draft'}</span></p></div><div className="flex flex-wrap gap-2"><button onClick={() => setTestOpen(true)} disabled={!phones.length} className="inline-flex items-center gap-2 rounded-xl border border-emerald-200 bg-white px-4 py-2.5 text-sm font-semibold text-emerald-700 disabled:opacity-40"><PhoneCall size={16} /> Test Call</button><button onClick={() => void save()} disabled={busy} className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-40"><Save size={16} /> Save Changes</button><button onClick={() => void save(true)} disabled={busy || !voiceId} className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40">{busy ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />} Publish</button></div></header>
    {(error || notice) && <p className={`mb-5 rounded-xl border p-3 text-sm ${error ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>{error || notice}</p>}
    <section className="mb-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="text-lg font-bold">Employee Overview</h2><div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4"><label className="text-xs font-semibold text-slate-500">Employee<input value={name} onChange={e => setName(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-normal text-slate-900" /></label><div><p className="text-xs font-semibold text-slate-500">Role / Purpose</p><p className="mt-1 text-sm text-slate-900">{String(config.role ?? employee.purpose)}</p></div><div><p className="text-xs font-semibold text-slate-500">Business</p><p className="mt-1 text-sm text-slate-900">{businessName || 'Business details in requirement'}</p></div><div><p className="text-xs font-semibold text-slate-500">Call Type</p><p className="mt-1 text-sm capitalize text-slate-900">{employee.call_type}</p></div></div><div className="mt-4"><label className="text-xs font-semibold text-slate-500">Language<select value={language} onChange={e => setLanguage(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-normal text-slate-900">{languages.map(item => <option key={item}>{item}</option>)}</select></label></div></section>
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,.65fr)]"><section className="space-y-6"><div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><p className="text-xs font-bold uppercase tracking-widest text-slate-400">Call Script · {language}</p><h2 className="mt-1 text-2xl font-bold">Call Script</h2><div className="mt-5 space-y-4">{sectionNames.map((section, index) => <label key={section} className="block rounded-xl border border-slate-200 p-4"><span className="text-xs font-bold uppercase tracking-wider text-blue-600">{index + 1}. {section}</span><textarea value={script[section] ?? ''} onChange={e => setConfig(current => ({ ...current, call_script: { ...(current.call_script ?? {}), [section]: e.target.value }, opening: section === 'Greeting & Intro' ? e.target.value : current.opening }))} rows={3} className="mt-2 w-full border-0 p-0 text-sm leading-6 outline-none" /></label>)}</div><WorkspaceExtensions employeeId={id} config={config} onChange={setConfig} /></div></section><aside className="space-y-6"><section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="text-lg font-bold">Shabdha</h2><p className="mt-1 text-sm text-slate-500">Tell Shabdha what you’d like to improve.</p>{!session ? <button onClick={() => void startChat()} disabled={busy} className="mt-4 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40"><MessageCircle size={15} className="mr-2 inline" /> Start refinement</button> : <><div className="mt-4 max-h-72 space-y-3 overflow-y-auto">{session.messages.map((m, i) => <p key={i} className={`rounded-xl p-3 text-sm ${m.role === 'assistant' ? 'bg-slate-100 text-slate-700' : 'ml-5 bg-blue-600 text-white'}`}>{m.content}</p>)}</div>{session.current_question && <div className="mt-4 flex gap-2"><input value={answer} onChange={e => setAnswer(e.target.value)} onKeyDown={e => e.key === 'Enter' && void sendAnswer()} placeholder="What should change?" className="min-w-0 flex-1 rounded-xl border border-slate-200 px-3 py-2 text-sm" /><button onClick={() => void sendAnswer()} disabled={busy || !answer.trim()} className="rounded-xl bg-blue-600 px-3 py-2 text-sm font-semibold text-white">Send</button></div>}</>}</section><section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="text-lg font-bold">Voice</h2><p className="mt-1 text-sm text-slate-500">Selected voice and language behavior.</p><label className="mt-4 block text-sm font-semibold">Selected Voice<select value={voiceId} onChange={e => setVoiceId(e.target.value)} className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 font-normal"><option value="">Select a voice</option>{languageVoices.map(v => <option key={v.id} value={v.id}>{v.name} · {v.gender}</option>)}</select></label><p className="mt-3 text-sm text-slate-600">Language: <strong>{language}</strong></p></section></aside></div>
    {testOpen && <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/40 p-4"><section className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl"><div className="flex items-start justify-between"><div><h2 className="text-lg font-bold">Test Call</h2><p className="mt-1 text-sm text-slate-500">Use a tenant-owned calling number.</p></div><button onClick={() => setTestOpen(false)} className="text-sm text-slate-500">Close</button></div><label className="mt-5 block text-sm font-semibold">Calling number<select value={fromPhone} onChange={e => setFromPhone(e.target.value)} className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-3 font-normal"><option value="">Select a number</option>{phones.map(phone => <option key={phone.id} value={phone.id}>{phone.label || phone.e164_number} · {phone.e164_number}</option>)}</select></label><label className="mt-4 block text-sm font-semibold">Destination number<input value={destination} onChange={e => setDestination(e.target.value)} placeholder="+919876543210" className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-3 font-normal" /></label><button onClick={() => void testCall()} disabled={calling || !fromPhone || !destination.trim()} className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white disabled:opacity-40">{calling && <Loader2 size={16} className="animate-spin" />} {calling ? 'Starting...' : 'Start test call'}</button></section></div>}
  </div></main>;
}

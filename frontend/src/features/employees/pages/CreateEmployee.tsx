import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Check, FileText, Loader2, MessageCircle, Plus, Sparkles, Zap } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { backendJson } from '../../../services/backend/api';

const languages = [
  'English', 'Hindi', 'Telugu', 'Tamil', 'Kannada',
  'Malayalam', 'Marathi', 'Bengali', 'Gujarati', 'Punjabi', 'Odia', 'Assamese',
];

type Suggestion = { question: string; reason: string };
type Session = {
  employee_id: string;
  current_question: string | null;
  suggested_questions: Suggestion[];
  consumed_questions: string[];
  extracted_configuration: Record<string, unknown>;
  is_complete: boolean;
  llm_used: boolean;
  answers?: Array<{ answer: string }>;
};
type Employee = { id: string; name: string; language: string; creation_mode?: 'chat' | 'prompt'; configuration?: Record<string, unknown> | null };
type Voice = { id: string; name: string; tier: string; gender: string; supports_cloning: boolean };
type Template = { id: string; name: string; category: string; short_description: string; purpose: string; default_language: string; placeholders: Array<{ key: string; label: string; description: string; type: string; required: boolean; validation?: Record<string, unknown> }> };

export default function CreateEmployee() {
  const navigate = useNavigate();
  const { id } = useParams();
  const ref = useRef<HTMLTextAreaElement>(null);

  const [language, setLanguage] = useState('');
  const [name, setName] = useState('');
  const [employeeId, setEmployeeId] = useState(id ?? '');
  const [session, setSession] = useState<Session | null>(null);
  const [answeredQuestions, setAnsweredQuestions] = useState(0);
  const [brief, setBrief] = useState('');
  const [busy, setBusy] = useState(Boolean(id));
  const [error, setError] = useState('');
  const [review, setReview] = useState(false);
  const [addingQuestion, setAddingQuestion] = useState<string | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceError, setVoiceError] = useState('');
  const [voiceId, setVoiceId] = useState('');
  const [builderMode, setBuilderMode] = useState<'template' | 'chat' | 'prompt'>('chat');
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<Template | null>(null);
  const [templateValues, setTemplateValues] = useState<Record<string, string>>({});
  const [directPrompt, setDirectPrompt] = useState('');

  const back = () => navigate('/employees');

  const loadSession = async (eid: string) => {
    const next = await backendJson<Session>(`/employees/${eid}/interview/start`, {
      method: 'POST',
      body: '{}',
    });
    setSession(next);
    const savedVoice = next.extracted_configuration?.voice;
    if (savedVoice && typeof savedVoice === 'object' && 'id' in savedVoice) setVoiceId(String(savedVoice.id));
    setAnsweredQuestions(next.answers?.length ?? 0);
    setBrief(next.answers?.at(-1)?.answer ?? '');
  };

  const start = async () => {
    if (!name.trim() || !language || (builderMode === 'prompt' && !directPrompt.trim())) return;
    setBusy(true);
    try {
      const employee = await backendJson<Employee>('/employees', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(), language, creation_mode: builderMode,
          ...(builderMode === 'prompt' ? { direct_prompt: directPrompt.trim() } : {}),
        }),
      });
      setEmployeeId(employee.id);
      window.history.replaceState(null, '', `/employees/${employee.id}`);
      if (builderMode === 'prompt') {
        setSession({ employee_id: employee.id, current_question: null, suggested_questions: [], consumed_questions: [], extracted_configuration: { direct_prompt: directPrompt.trim(), system_prompt: directPrompt.trim() }, is_complete: true, llm_used: false });
        setReview(true);
      } else {
        await loadSession(employee.id);
      }
    } catch {
      setError('Unable to start Shabdha right now. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void backendJson<Template[]>('/employees/templates').then(setTemplates).catch(() => undefined);
    void backendJson<Voice[]>('/employees/voice-catalog').then(items => {
      setVoices(items);
      console.info('[voice-catalog]', { endpoint: '/employees/voice-catalog', status: 200, count: items.length, language });
    }).catch(error => {
      setVoiceError(error instanceof Error ? error.message : 'Voice catalog could not be loaded.');
      console.warn('[voice-catalog]', { endpoint: '/employees/voice-catalog', status: (error as { status?: number }).status ?? 'network-error', language });
    });
  }, [language]);

  useEffect(() => {
    if (!id) return;
    void (async () => {
      try {
        const employee = await backendJson<Employee>(`/employees/${id}`);
        setName(employee.name);
        setLanguage(employee.language);
        setEmployeeId(employee.id);
        setBuilderMode(employee.creation_mode === 'prompt' ? 'prompt' : 'chat');
        const savedPrompt = employee.configuration?.direct_prompt || employee.configuration?.system_prompt;
        if (typeof savedPrompt === 'string') setDirectPrompt(savedPrompt);
        await loadSession(employee.id);
      } catch {
        setError('Unable to resume this employee builder.');
      } finally {
        setBusy(false);
      }
    })();
  }, [id]);

  const updateBrief = async () => {
    if (!session || !brief.trim() || busy) return;
    setBusy(true);
    setError('');
    try {
      const next = await backendJson<Session>(`/employees/${employeeId}/interview/answer`, {
        method: 'POST',
        body: JSON.stringify({ question: 'What is this employee for?', answer: brief }),
      });
      setSession(next);
      setAnsweredQuestions(next.answers?.length ?? 0);
    } catch {
      setError('Shabdha could not update your brief. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  const add = async (suggestion: Suggestion) => {
    // Immediately remove from UI
    setAddingQuestion(suggestion.question);
    setSession(prev =>
      prev
        ? {
            ...prev,
            suggested_questions: prev.suggested_questions.filter(
              s => s.question !== suggestion.question,
            ),
            consumed_questions: [...(prev.consumed_questions ?? []), suggestion.question],
          }
        : prev,
    );

    // Persist consumed state server-side
    try {
      await backendJson<Session>(`/employees/${employeeId}/interview/consume`, {
        method: 'POST',
        body: JSON.stringify({ question: suggestion.question }),
      });
    } catch {
      // Non-fatal: UI already removed it
    }

    // Insert into brief and move cursor
    const appended = `${brief.trimEnd()}\n\n${suggestion.question}\n`;
    setBrief(appended);
    requestAnimationFrame(() => {
      ref.current?.focus();
      const end = ref.current?.value.length ?? 0;
      ref.current?.setSelectionRange(end, end);
    });
    setAddingQuestion(null);
  };

  const publish = async () => {
    if (!session) return;
    setBusy(true);
    setError('');
    try {
      const selectedVoice = voices.find(voice => voice.id === voiceId);
      const promptForPublish = String(session.extracted_configuration?.direct_prompt || directPrompt).trim();
      const configuration = { ...(session.extracted_configuration ?? {}), name: name.trim(), language, creation_mode: builderMode === 'template' ? 'chat' : builderMode, ...(selectedTemplate ? { selected_template_id: selectedTemplate.id, selected_template_version: 1, template_values: templateValues } : {}), ...(builderMode === 'prompt' ? { direct_prompt: promptForPublish, system_prompt: promptForPublish } : {}), ...(selectedVoice ? { voice: { id: selectedVoice.id, name: selectedVoice.name, tier: selectedVoice.tier, gender: selectedVoice.gender } } : {}) };
      await backendJson(`/employees/${employeeId}`, {
        method: 'PATCH',
        body: JSON.stringify({ configuration }),
      });
      await backendJson(`/employees/${employeeId}/publish`, { method: 'POST' });
      navigate('/employees');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Please try again.';
      setError(`Unable to publish this employee. ${message}`);
    } finally {
      setBusy(false);
    }
  };

  const startTemplate = async () => {
    if (!selectedTemplate || !name.trim() || selectedTemplate.placeholders.some(p => p.required && !templateValues[p.key]?.trim())) return;
    setBusy(true); setError('');
    try {
      const employee = await backendJson<Employee>('/employees', { method: 'POST', body: JSON.stringify({ name: name.trim(), language: language || 'Telugu', creation_mode: 'chat' }) });
      setEmployeeId(employee.id); window.history.replaceState(null, '', `/employees/${employee.id}`);
      const rendered = await backendJson<Record<string, unknown>>('/employees/templates/render', { method: 'POST', body: JSON.stringify({ template_id: selectedTemplate.id, values: templateValues, language: language || 'Telugu' }) });
      setSession({ employee_id: employee.id, current_question: null, suggested_questions: [], consumed_questions: [], extracted_configuration: rendered, is_complete: true, llm_used: false });
      setReview(true);
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to render template.'); } finally { setBusy(false); }
  };

  const canBuild = builderMode === 'prompt' ? directPrompt.trim().length >= 20 : answeredQuestions >= 3;
  const hasMinimumAnswers = answeredQuestions >= 3;

  // ── Language selection screen ──────────────────────────────────────────────
  if (!employeeId) {
    return (
      <main className="flex-1 bg-slate-50 overflow-y-auto">
        <div className="max-w-5xl mx-auto p-6 lg:pt-14">
          <button onClick={back} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition mb-8">
            <ArrowLeft className="w-4 h-4" /> Back
          </button>
          <div className="grid lg:grid-cols-[200px_1fr] gap-8">
            <aside className="hidden lg:block">
              <p className="text-xs font-semibold uppercase tracking-widest text-gray-400">Employee setup</p>
              <ol className="mt-6 space-y-4 text-sm">
                <li className="flex items-center gap-3 font-semibold text-blue-700">
                  <span className="w-6 h-6 rounded-full bg-blue-600 text-white grid place-items-center text-xs font-bold">1</span>
                  Choose language
                </li>
                <li className="flex items-center gap-3 text-gray-400">
                  <span className="w-6 h-6 rounded-full border-2 border-gray-200 grid place-items-center text-xs">2</span>
                  Brief Shabdha
                </li>
                <li className="flex items-center gap-3 text-gray-400">
                  <span className="w-6 h-6 rounded-full border-2 border-gray-200 grid place-items-center text-xs">3</span>
                  Review &amp; publish
                </li>
              </ol>
            </aside>
            <section className="bg-white border border-gray-200 rounded-2xl shadow-sm p-8">
              <h1 className="text-2xl font-bold text-gray-900">Create your AI employee</h1>
              <p className="mt-2 text-sm text-gray-500">How would you like to create the employee?</p>
              <div className="grid sm:grid-cols-3 gap-3 mt-5">
                {[['template', 'Use a Template', 'Start from a professionally designed workflow'], ['chat', 'Chat with Shabdha', 'Build it conversationally'], ['prompt', 'Paste complete prompt', 'Use your own instructions']].map(([mode, title, help]) => <button key={mode} type="button" onClick={() => { setBuilderMode(mode as 'template' | 'chat' | 'prompt'); if (mode === 'template') setLanguage('Telugu'); }} className={`rounded-xl border p-4 text-left ${builderMode === mode ? 'border-blue-600 bg-blue-50' : 'border-gray-200'}`}><span className="font-semibold text-gray-900">{title}</span><span className="block mt-1 text-xs text-gray-500">{help}</span></button>)}
              </div>
              {builderMode === 'template' ? <>
                <label className="block mt-7 text-sm font-semibold text-gray-700">Employee name<input value={name} onChange={e => setName(e.target.value)} className="mt-2 w-full border border-gray-200 rounded-xl px-3 py-3" placeholder="e.g. Life Hospitals Receptionist" /></label>
                <div className="grid md:grid-cols-2 gap-3 mt-5">{templates.map(t => <button key={t.id} type="button" onClick={() => setSelectedTemplate(t)} className={`rounded-xl border p-4 text-left ${selectedTemplate?.id === t.id ? 'border-blue-600 bg-blue-50' : 'border-gray-200 hover:border-blue-300'}`}><span className="text-xs text-blue-600 font-semibold">{t.category}</span><h2 className="mt-1 font-semibold text-gray-900">{t.name}</h2><p className="mt-1 text-xs text-gray-500">{t.short_description}</p><p className="mt-2 text-xs text-gray-600">Default language: {t.default_language}</p></button>)}</div>
                {selectedTemplate && <div className="mt-6 border-t pt-5"><h2 className="font-semibold text-gray-900">Customize your employee</h2><div className="grid md:grid-cols-2 gap-4 mt-4">{selectedTemplate.placeholders.map(p => <label key={p.key} className="text-sm font-semibold text-gray-700">{p.label}{p.required && <span className="text-rose-500"> *</span>}<span className="block text-xs font-normal text-gray-500 mt-1">{p.description}</span><textarea required={p.required} value={templateValues[p.key] ?? ''} onChange={e => setTemplateValues(v => ({ ...v, [p.key]: e.target.value }))} className="mt-2 w-full border border-gray-200 rounded-xl px-3 py-2.5 text-sm" rows={2} /></label>)}</div><button onClick={() => void startTemplate()} disabled={busy || !name.trim() || selectedTemplate.placeholders.some(p => p.required && !templateValues[p.key]?.trim())} className="mt-6 bg-blue-600 text-white rounded-xl px-5 py-3 text-sm font-semibold disabled:opacity-50">Preview employee</button></div>}
              </> : <>
            <section className="bg-white border border-gray-200 rounded-2xl shadow-sm p-8">
              <p className="text-xs font-semibold text-blue-600 uppercase tracking-widest">Step 1 of 3</p>
              <h1 className="mt-2 text-2xl font-bold text-gray-900">What language should they speak?</h1>
              <p className="mt-2 text-sm text-gray-500">Choose the primary language Shabdha should use for this employee.</p>
              <label className="block mt-8 text-sm font-semibold text-gray-700">Employee name
                <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Maya, Sales Assistant" className="mt-2 w-full border border-gray-200 rounded-xl px-3 py-3 text-sm font-normal focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400" />
              </label>
              <div className="grid sm:grid-cols-3 gap-3 mt-8">
                {languages.map(item => (
                  <button
                    key={item}
                    onClick={() => setLanguage(item)}
                    className={`border rounded-xl p-3 text-left text-sm font-medium transition-all ${
                      language === item
                        ? 'border-blue-600 bg-blue-50 text-blue-800 shadow-sm'
                        : 'border-gray-200 text-gray-700 hover:border-blue-300 hover:bg-blue-50/50'
                    }`}
                  >
                    {item}
                  </button>
                ))}
              </div>
              <div className="mt-8">
                <p className="text-sm font-semibold text-gray-700">How would you like to create the employee?</p>
                <div className="grid sm:grid-cols-2 gap-3 mt-3">
                  <button type="button" onClick={() => setBuilderMode('chat')} className={`rounded-xl border p-4 text-left transition ${builderMode === 'chat' ? 'border-blue-600 bg-blue-50' : 'border-gray-200 hover:border-blue-300'}`}>
                    <span className="flex items-center gap-2 font-semibold text-gray-900"><MessageCircle className="w-4 h-4 text-blue-600" /> Chat with Shabdha</span>
                    <span className="block mt-1 text-xs text-gray-500">Answer guided questions and build the employee step by step.</span>
                  </button>
                  <button type="button" onClick={() => setBuilderMode('prompt')} className={`rounded-xl border p-4 text-left transition ${builderMode === 'prompt' ? 'border-blue-600 bg-blue-50' : 'border-gray-200 hover:border-blue-300'}`}>
                    <span className="flex items-center gap-2 font-semibold text-gray-900"><FileText className="w-4 h-4 text-blue-600" /> Paste complete prompt</span>
                    <span className="block mt-1 text-xs text-gray-500">Send your finished voice-agent instructions directly to OmniDimension.</span>
                  </button>
                </div>
                {builderMode === 'prompt' && (
                  <textarea value={directPrompt} onChange={e => setDirectPrompt(e.target.value)} placeholder="Paste the complete instructions for this employee..." className="mt-3 w-full min-h-56 rounded-xl border border-gray-200 p-4 text-sm leading-6 focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400" />
                )}
              </div>
              <button
                onClick={() => void start()}
                disabled={!name.trim() || !language || busy || (builderMode === 'prompt' && directPrompt.trim().length < 20)}
                className="mt-8 bg-blue-600 hover:bg-blue-700 text-white rounded-xl px-6 py-3 text-sm font-semibold disabled:opacity-50 transition flex items-center gap-2"
              >
                {busy ? <><Loader2 className="w-4 h-4 animate-spin" /> Starting Shabdha…</> : 'Continue to Shabdha →'}
              </button>
              {error && <p className="mt-4 text-sm text-rose-600">{error}</p>}
            </section>
              </>}
            </section>
          </div>
        </div>
      </main>
    );
  }

  // ── Review screen ──────────────────────────────────────────────────────────
  if (review) {
    const reviewConfig = session?.extracted_configuration ?? {};
    const reviewPrompt = String(reviewConfig.direct_prompt || reviewConfig.system_prompt || '');
    const reviewFields = Object.entries(reviewConfig).filter(([key]) => !['llm_provider', 'llm_model', 'direct_prompt', 'system_prompt'].includes(key));
    return (
      <main className="flex-1 bg-slate-50 overflow-y-auto p-6">
        <section className="max-w-4xl mx-auto bg-white border border-gray-200 rounded-2xl shadow-sm p-8">
          <button onClick={() => setReview(false)} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition mb-6">
            <ArrowLeft className="w-4 h-4" /> Back to Shabdha
          </button>
          <p className="text-xs font-semibold text-blue-600 uppercase tracking-widest">Step 3 of 3</p>
          <h1 className="mt-2 text-2xl font-bold text-gray-900">Review your AI employee</h1>
          <p className="mt-1 text-sm text-gray-500">Review the complete instructions and operating rules before they are sent to OmniDimension.</p>
          <div className="mt-6 grid sm:grid-cols-3 gap-3">
            <div className="rounded-xl bg-blue-50 border border-blue-100 p-4"><p className="text-xs text-blue-600 font-semibold uppercase tracking-wide">Language</p><p className="mt-1 font-semibold text-gray-900">{language}</p></div>
            <div className="rounded-xl bg-emerald-50 border border-emerald-100 p-4"><p className="text-xs text-emerald-600 font-semibold uppercase tracking-wide">Creation mode</p><p className="mt-1 font-semibold text-gray-900">{builderMode === 'prompt' ? 'Direct prompt' : 'Shabdha chat'}</p></div>
            <div className="rounded-xl bg-violet-50 border border-violet-100 p-4"><p className="text-xs text-violet-600 font-semibold uppercase tracking-wide">Instruction size</p><p className="mt-1 font-semibold text-gray-900">{reviewPrompt.length.toLocaleString()} characters</p></div>
          </div>
          {reviewPrompt && <article className="mt-5 rounded-2xl border border-blue-200 bg-blue-50/40 p-5">
            <div className="flex items-center justify-between gap-3"><div><h2 className="font-semibold text-gray-900">Complete Omni instructions</h2><p className="mt-1 text-xs text-gray-500">This is the prompt that will be placed in Omni’s context instructions.</p></div><span className="text-xs font-medium text-blue-700">{reviewPrompt.split(/\s+/).filter(Boolean).length} words</span></div>
            <textarea value={reviewPrompt} onChange={e => setSession(prev => prev ? ({ ...prev, extracted_configuration: { ...prev.extracted_configuration, direct_prompt: e.target.value, system_prompt: e.target.value } }) : prev)} className="mt-4 w-full min-h-64 rounded-xl border border-blue-200 bg-white p-4 text-sm leading-6 text-gray-800 focus:outline-none focus:ring-2 focus:ring-blue-500/30" />
          </article>}
          <div className="grid md:grid-cols-2 gap-4 mt-5">
            {reviewFields
              .map(([key, value]) => (
                <article key={key} className="border border-gray-200 bg-white rounded-xl p-4 shadow-sm">
                  <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">{key.replaceAll('_', ' ')}</h2>
                  {Array.isArray(value) ? (
                    <textarea
                      value={value.join('\n')}
                      onChange={e => setSession(prev => prev ? ({ ...prev, extracted_configuration: { ...prev.extracted_configuration, [key]: e.target.value.split('\n').map(item => item.trim()).filter(Boolean) } }) : prev)}
                      className="mt-2 w-full min-h-24 text-sm text-gray-800 border border-gray-200 rounded-lg p-2"
                    />
                  ) : (
                    <textarea
                      value={typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value ?? '')}
                      onChange={e => setSession(prev => prev ? ({ ...prev, extracted_configuration: { ...prev.extracted_configuration, [key]: e.target.value } }) : prev)}
                      className="mt-2 w-full min-h-20 text-sm text-gray-800 border border-gray-200 rounded-lg p-2"
                    />
                  )}
                </article>
              ))}
            <article className="border border-gray-200 rounded-xl p-4">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">Language</h2>
              <input value={language} onChange={e => setLanguage(e.target.value)} className="mt-2 w-full text-sm text-gray-800 border border-gray-200 rounded-lg p-2" />
            </article>
            <article className="border border-gray-200 rounded-xl p-4 md:col-span-2">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-gray-400">Voice</h2>
              {voices.length > 0 ? (
                <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-2 mt-2">
                  {voices.map(voice => (
                    <button type="button" key={voice.id} onClick={() => setVoiceId(voice.id)} className={`rounded-lg border p-3 text-left text-sm ${voiceId === voice.id ? 'border-blue-600 bg-blue-50' : 'border-gray-200'}`}>
                      <span className="font-semibold">{voice.name}</span>
                      <span className="block text-xs text-gray-500 capitalize">{voice.tier} · {voice.gender}</span>
                    </button>
                  ))}
                </div>
              ) : <p className="mt-2 text-sm text-gray-500">{voiceError || 'No verified OmniDimension voices are configured yet.'}</p>}
            </article>
          </div>
          <button
            onClick={() => void publish()}
            disabled={busy}
            className="mt-8 bg-blue-600 hover:bg-blue-700 text-white rounded-xl px-6 py-3 text-sm font-semibold disabled:opacity-50 transition flex items-center gap-2"
          >
            {busy ? <><Loader2 className="w-4 h-4 animate-spin" /> Publishing…</> : 'Publish employee'}
          </button>
          {error && <p className="mt-4 text-sm text-rose-600">{error}</p>}
        </section>
      </main>
    );
  }

  // ── Builder screen ─────────────────────────────────────────────────────────
  return (
    <main className="flex-1 bg-slate-50 overflow-y-auto">
      <div className="max-w-6xl mx-auto p-6 grid lg:grid-cols-[200px_1fr] gap-8">
        {/* Sidebar */}
        <aside className="hidden lg:block pt-2">
          <button onClick={back} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition mb-8">
            <ArrowLeft className="w-4 h-4" /> Back
          </button>
          <p className="text-xs font-semibold uppercase tracking-widest text-gray-400">Employee setup</p>
          <ol className="mt-6 space-y-4 text-sm">
            <li className="flex items-center gap-3 text-gray-500">
              <span className="w-6 h-6 rounded-full bg-emerald-100 text-emerald-700 grid place-items-center">
                <Check className="w-3.5 h-3.5" />
              </span>
              Choose language
            </li>
            <li className="flex items-center gap-3 font-semibold text-gray-900">
              <span className="w-6 h-6 rounded-full bg-blue-600 text-white grid place-items-center text-xs font-bold">2</span>
              Brief Shabdha
            </li>
            <li className="flex items-center gap-3 text-gray-400">
              <span className="w-6 h-6 rounded-full border-2 border-gray-200 grid place-items-center text-xs">3</span>
              Review &amp; publish
            </li>
          </ol>
          {session?.llm_used === false && (
            <div className="mt-8 p-3 bg-amber-50 border border-amber-200 rounded-xl text-xs text-amber-700">
              Using offline mode — configure LLM credentials for AI-powered suggestions.
            </div>
          )}
        </aside>

        {/* Main builder card */}
        <section className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          {/* Shabdha header */}
          <header className="px-8 py-6 border-b border-gray-100 bg-gradient-to-r from-blue-50/60 to-white">
            <button onClick={back} className="lg:hidden mb-4 flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition">
              <ArrowLeft className="w-4 h-4" /> Back
            </button>
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-2xl bg-blue-600 text-white grid place-items-center shadow-sm">
                <Sparkles className="w-6 h-6" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-gray-900">Meet Shabdha</h1>
                <p className="text-sm text-gray-500 mt-0.5">
                  I'll help you shape a powerful AI employee — one question at a time.
                </p>
              </div>
            </div>
          </header>

          <div className="px-8 py-7">
            {/* Question */}
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-gray-900">What is this employee for?</h2>
              <p className="mt-1 text-sm text-gray-500">
                Describe the role, purpose, and any important details. Your brief grows as you add more context.
              </p>
            </div>

            {/* Brief editor */}
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">Your brief</label>
              <p className="text-xs text-gray-400 mb-3">
                Write everything Shabdha should know about this employee's role, customers, and goals.
              </p>
              <textarea
                ref={ref}
                value={brief}
                disabled={busy}
                onChange={e => setBrief(e.target.value)}
                placeholder="e.g. I need a voice employee that calls people who enquire about our dental clinic and books appointments. It should ask about the treatment they need, offer available slots, and transfer complex cases to our front desk."
                className="w-full min-h-52 max-h-96 border border-gray-200 rounded-xl p-4 text-sm text-gray-800 leading-7 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-400 disabled:bg-gray-50 disabled:text-gray-500 resize-y transition"
              />
            </div>

            {/* Actions */}
            <div className="mt-4 flex items-center justify-between gap-4">
              <p className="text-xs text-gray-400">
                Save your brief so Shabdha can find the next missing details.
              </p>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => void updateBrief()}
                  disabled={busy || !brief.trim()}
                  className="bg-blue-600 hover:bg-blue-700 text-white rounded-xl px-5 py-2.5 text-sm font-semibold disabled:opacity-50 transition flex items-center gap-2"
                >
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                  {busy ? 'Thinking…' : 'Update brief'}
                </button>
                <button
                  onClick={() => setReview(true)}
                  disabled={busy || !hasMinimumAnswers || !canBuild}
                  title={!hasMinimumAnswers ? 'Answer at least 3 questions first.' : !canBuild ? 'Shabdha is still gathering the required context.' : 'Review and build your employee.'}
                  className="bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl px-5 py-2.5 text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition"
                >
                  Build my employee →
                </button>
              </div>
            </div>

            {error && (
              <p className="mt-4 text-sm text-rose-600 bg-rose-50 border border-rose-200 rounded-xl px-4 py-3">
                {error}
              </p>
            )}

            {/* Completion state */}
            {canBuild ? (
              <div className="mt-8 p-5 bg-emerald-50 border border-emerald-200 rounded-2xl">
                <div className="flex items-center gap-3 mb-3">
                  <span className="w-8 h-8 rounded-full bg-emerald-600 text-white grid place-items-center">
                    <Check className="w-4 h-4" />
                  </span>
                  <p className="font-semibold text-emerald-900">Do you have anything more you'd like me to know?</p>
                </div>
                <p className="text-sm text-emerald-700 mb-4">
                  Shabdha has enough to build your employee. You can add more context above or proceed to review.
                </p>
                <button
                  onClick={() => setReview(true)}
                  className="bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl px-5 py-2.5 text-sm font-semibold transition"
                >
                  Build my employee →
                </button>
              </div>
            ) : (
              /* Suggestions — only shown before ready to build */
              !canBuild && session?.suggested_questions && session.suggested_questions.length > 0 && (
                <div className="mt-8">
                  <div className="flex items-center gap-2 mb-3">
                    <Sparkles className="w-4 h-4 text-blue-500" />
                    <p className="text-xs font-semibold uppercase tracking-widest text-gray-500">
                      Shabdha suggests
                    </p>
                  </div>
                  <div className="space-y-2">
                    {session.suggested_questions.map(s => (
                      <div
                        key={s.question}
                        className="group flex items-start gap-3 border border-gray-200 rounded-xl px-4 py-3.5 hover:border-blue-300 hover:bg-blue-50/40 transition-all"
                      >
                        <span className="mt-0.5 w-5 h-5 rounded-full bg-blue-100 text-blue-600 grid place-items-center shrink-0 text-xs font-bold">
                          ✦
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-gray-800">{s.question}</p>
                          {s.reason && (
                            <p className="mt-0.5 text-xs text-gray-500">{s.reason}</p>
                          )}
                        </div>
                        <button
                          onClick={() => void add(s)}
                          disabled={addingQuestion === s.question || busy}
                          className="shrink-0 flex items-center gap-1 text-xs font-semibold text-blue-700 border border-blue-200 rounded-lg px-2.5 py-1.5 hover:bg-blue-600 hover:text-white hover:border-blue-600 disabled:opacity-40 transition-all"
                        >
                          <Plus className="w-3 h-3" />
                          Add
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

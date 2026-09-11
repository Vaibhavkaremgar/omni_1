import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Check, Loader2, Plus, Sparkles, Zap } from 'lucide-react';
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
type Employee = { id: string; name: string; language: string };

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

  const back = () => navigate('/employees');

  const loadSession = async (eid: string) => {
    const next = await backendJson<Session>(`/employees/${eid}/interview/start`, {
      method: 'POST',
      body: '{}',
    });
    setSession(next);
    setAnsweredQuestions(next.answers?.length ?? 0);
    setBrief(next.answers?.at(-1)?.answer ?? '');
  };

  const start = async () => {
    if (!name.trim() || !language) return;
    setBusy(true);
    try {
      const employee = await backendJson<Employee>('/employees', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim(), language }),
      });
      setEmployeeId(employee.id);
      window.history.replaceState(null, '', `/employees/${employee.id}`);
      await loadSession(employee.id);
    } catch {
      setError('Unable to start Shabdha right now. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!id) return;
    void (async () => {
      try {
        const employee = await backendJson<Employee>(`/employees/${id}`);
        setName(employee.name);
        setLanguage(employee.language);
        setEmployeeId(employee.id);
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
      const configuration = { ...(session.extracted_configuration ?? {}), name: name.trim(), language };
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

  const canBuild = answeredQuestions >= 3;
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
              <button
                onClick={() => void start()}
                disabled={!name.trim() || !language || busy}
                className="mt-8 bg-blue-600 hover:bg-blue-700 text-white rounded-xl px-6 py-3 text-sm font-semibold disabled:opacity-50 transition flex items-center gap-2"
              >
                {busy ? <><Loader2 className="w-4 h-4 animate-spin" /> Starting Shabdha…</> : 'Continue to Shabdha →'}
              </button>
              {error && <p className="mt-4 text-sm text-rose-600">{error}</p>}
            </section>
          </div>
        </div>
      </main>
    );
  }

  // ── Review screen ──────────────────────────────────────────────────────────
  if (review) {
    return (
      <main className="flex-1 bg-slate-50 overflow-y-auto p-6">
        <section className="max-w-4xl mx-auto bg-white border border-gray-200 rounded-2xl shadow-sm p-8">
          <button onClick={() => setReview(false)} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition mb-6">
            <ArrowLeft className="w-4 h-4" /> Back to Shabdha
          </button>
          <p className="text-xs font-semibold text-blue-600 uppercase tracking-widest">Step 3 of 3</p>
          <h1 className="mt-2 text-2xl font-bold text-gray-900">Review your AI employee</h1>
          <p className="mt-1 text-sm text-gray-500">Check the details below before publishing.</p>
          <div className="grid md:grid-cols-2 gap-4 mt-6">
            {Object.entries(session?.extracted_configuration ?? {})
              .filter(([key]) => !['llm_provider', 'llm_model'].includes(key))
              .map(([key, value]) => (
                <article key={key} className="border border-gray-200 rounded-xl p-4">
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

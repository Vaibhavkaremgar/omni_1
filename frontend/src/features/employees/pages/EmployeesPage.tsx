import { useEffect, useState } from 'react';
import { Globe, Plus, Trash2, Users, Zap } from 'lucide-react';
import { Link } from 'react-router-dom';
import { backendJson } from '../../../services/backend/api';

type Employee = {
  id: string;
  name: string;
  purpose: string;
  language: string;
  status: string;
  configuration?: Record<string, unknown>;
};

const taskCount = (e: Employee) =>
  Array.isArray(e.configuration?.tasks)
    ? (e.configuration!.tasks as unknown[]).length
    : Array.isArray(e.configuration?.goals)
    ? (e.configuration!.goals as unknown[]).length
    : 0;

export default function EmployeesPage() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [target, setTarget] = useState<Employee | null>(null);
  const [deleting, setDeleting] = useState('');
  const [publishing, setPublishing] = useState('');

  const load = async () => {
    try {
      setEmployees(await backendJson<Employee[]>('/employees'));
    } catch {
      setError('Unable to load employees.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const t = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(t);
  }, []);

  const publish = async (employee: Employee) => {
    setPublishing(employee.id);
    setError('');
    try {
      const updated = await backendJson<Employee>(`/employees/${employee.id}/publish`, { method: 'POST' });
      setEmployees(items => items.map(item => (item.id === updated.id ? updated : item)));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Please try again.';
      setError(`Unable to publish employee. ${message}`);
    } finally {
      setPublishing('');
    }
  };

  const remove = async () => {
    if (!target) return;
    setDeleting(target.id);
    try {
      await backendJson(`/employees/${target.id}`, { method: 'DELETE' });
      setEmployees(items => items.filter(item => item.id !== target.id));
      setNotice('Employee deleted.');
      setTarget(null);
    } catch {
      setError('Unable to delete employee. Please try again.');
    } finally {
      setDeleting('');
    }
  };

  if (loading) {
    return (
      <div className="flex-1 bg-slate-50 overflow-y-auto">
        <header className="h-16 bg-white border-b border-gray-200 px-6 flex justify-between items-center">
          <div className="h-5 w-32 bg-gray-200 rounded animate-pulse" />
          <div className="h-9 w-36 bg-gray-200 rounded-xl animate-pulse" />
        </header>
        <main className="max-w-6xl mx-auto p-6">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="h-64 bg-white border border-gray-200 rounded-2xl animate-pulse" />
            ))}
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="flex-1 bg-slate-50 overflow-y-auto">
      <header className="h-16 bg-white border-b border-gray-200 px-6 flex justify-between items-center">
        <div>
          <h1 className="text-lg font-bold text-gray-900">My Employees</h1>
          <p className="text-xs text-gray-500">
            {employees.length} employee{employees.length === 1 ? '' : 's'}
          </p>
        </div>
        <Link
          to="/employees/new"
          className="bg-blue-600 hover:bg-blue-700 text-white rounded-xl px-4 py-2 text-sm font-semibold flex items-center gap-2 transition"
        >
          <Plus className="w-4 h-4" />
          Create Employee
        </Link>
      </header>

      <main className="max-w-6xl mx-auto p-6">
        {(error || notice) && (
          <p
            className={`mb-5 px-4 py-3 text-sm rounded-xl border ${
              error
                ? 'bg-rose-50 border-rose-200 text-rose-700'
                : 'bg-emerald-50 border-emerald-200 text-emerald-700'
            }`}
          >
            {error || notice}
          </p>
        )}

        {employees.length === 0 ? (
          <div className="bg-white border border-gray-200 rounded-2xl p-16 text-center shadow-sm">
            <div className="w-14 h-14 bg-blue-50 rounded-2xl grid place-items-center mx-auto mb-4">
              <Users className="w-7 h-7 text-blue-500" />
            </div>
            <h2 className="text-base font-semibold text-gray-900">No employees yet</h2>
            <p className="mt-1 text-sm text-gray-500">Create your first AI employee with Shabdha.</p>
            <Link
              to="/employees/new"
              className="inline-flex items-center gap-2 mt-5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl px-5 py-2.5 text-sm font-semibold transition"
            >
              <Plus className="w-4 h-4" /> Create Employee
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {employees.map(employee => {
              const tasks = taskCount(employee);
              const isPublished = employee.status === 'published';
              return (
                <article
                  key={employee.id}
                  className="bg-white border border-gray-200 rounded-2xl shadow-sm flex flex-col hover:shadow-md hover:-translate-y-0.5 transition-all duration-150"
                >
                  {/* Card top */}
                  <div className="p-5 flex-1">
                    <div className="flex items-start justify-between mb-4">
                      <div className="w-10 h-10 rounded-xl bg-blue-50 text-blue-600 grid place-items-center">
                        <Zap className="w-5 h-5" />
                      </div>
                      <span
                        className={`text-xs font-semibold rounded-full px-2.5 py-1 ${
                          isPublished
                            ? 'bg-emerald-100 text-emerald-700'
                            : 'bg-amber-100 text-amber-700'
                        }`}
                      >
                        {isPublished ? 'Published' : 'Draft'}
                      </span>
                    </div>

                    <h2 className="font-semibold text-gray-900 truncate">{employee.name}</h2>
                    <p className="mt-1.5 text-sm text-gray-500 line-clamp-3 leading-relaxed">
                      {employee.purpose}
                    </p>

                    <div className="mt-4 flex flex-wrap gap-2">
                      <span className="inline-flex items-center gap-1 text-xs text-gray-500 bg-gray-100 rounded-full px-2.5 py-1">
                        <Globe className="w-3 h-3" />
                        {employee.language}
                      </span>
                      {tasks > 0 && (
                        <span className="inline-flex items-center gap-1 text-xs text-blue-700 bg-blue-50 rounded-full px-2.5 py-1">
                          ✓ {tasks} task{tasks === 1 ? '' : 's'}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Card footer */}
                  <div className="px-5 py-4 border-t border-gray-100 flex items-center gap-2">
                    <Link
                      to={`/employees/${employee.id}`}
                      className="flex-1 border border-gray-200 rounded-xl py-2 text-center text-sm font-medium text-gray-700 hover:bg-gray-50 transition"
                    >
                      Open
                    </Link>
                    {!isPublished && (
                      <button
                        onClick={() => void publish(employee)}
                        disabled={publishing === employee.id}
                        className="bg-blue-600 hover:bg-blue-700 text-white rounded-xl px-4 py-2 text-sm font-medium disabled:opacity-50 transition"
                      >
                        {publishing === employee.id ? '…' : 'Publish'}
                      </button>
                    )}
                    <button
                      onClick={() => setTarget(employee)}
                      title="Delete employee"
                      className="w-9 h-9 border border-gray-200 rounded-xl grid place-items-center text-gray-400 hover:border-rose-300 hover:text-rose-600 hover:bg-rose-50 transition"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </main>

      {/* Delete confirmation modal */}
      {target && (
        <div className="fixed inset-0 z-50 bg-black/40 grid place-items-center p-4">
          <section className="bg-white rounded-2xl max-w-md w-full p-6 shadow-xl border border-gray-200">
            <h2 className="font-semibold text-lg text-gray-900">Delete this employee?</h2>
            <p className="mt-2 text-sm text-gray-600">
              Are you sure you want to delete <strong>{target.name}</strong>? This action cannot be undone.
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                disabled={Boolean(deleting)}
                onClick={() => setTarget(null)}
                className="border border-gray-200 rounded-xl px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 transition"
              >
                Cancel
              </button>
              <button
                disabled={Boolean(deleting)}
                onClick={() => void remove()}
                className="bg-rose-600 hover:bg-rose-700 text-white rounded-xl px-4 py-2 text-sm font-medium disabled:opacity-50 transition"
              >
                {deleting ? 'Deleting…' : 'Delete employee'}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

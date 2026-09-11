import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { backendJson } from '../../../services/backend/api';
import {
  Megaphone, Plus, ChevronRight,
} from 'lucide-react';

interface EmployeeSummary {
  id: string;
  name: string;
  purpose: string;
  language: string;
  status: string;
  is_ready: boolean;
}

interface Campaign {
  id: string;
  name: string;
  description: string | null;
  employee_id: string;
  employee: EmployeeSummary | null;
  status: string;
  contact_count: number;
  created_at: string;
  updated_at: string;
}

const statusConfig: Record<string, { color: string; label: string }> = {
  draft: { color: 'bg-gray-100 text-gray-600', label: 'Draft' },
  running: { color: 'bg-emerald-100 text-emerald-700', label: 'Running' },
  paused: { color: 'bg-amber-100 text-amber-700', label: 'Paused' },
  completed: { color: 'bg-blue-100 text-blue-700', label: 'Completed' },
  stopped: { color: 'bg-rose-100 text-rose-700', label: 'Stopped' },
  failed: { color: 'bg-rose-100 text-rose-700', label: 'Failed' },
};

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    backendJson<Campaign[]>('/campaigns')
      .then(data => setCampaigns(data || []))
      .catch(() => setError('Failed to load campaigns.'))
      .finally(() => setLoading(false));
  }, []);

  const formatTime = (iso: string) =>
    new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });

  if (loading) {
    return (
      <div className="flex-1 flex flex-col">
        <div className="h-16 border-b border-gray-200 px-6 flex items-center">
          <div className="h-4 w-40 bg-gray-200 rounded animate-pulse" />
        </div>
        <div className="flex-1 overflow-y-auto p-6">
          <div className="h-96 bg-gray-100 rounded-xl animate-pulse" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="h-16 border-b border-gray-200 px-6 flex items-center justify-between bg-white">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-violet-100 flex items-center justify-center">
            <Megaphone className="w-4 h-4 text-violet-600" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-gray-900">Bulk Campaigns</h1>
            <p className="text-xs text-gray-500">Upload contacts and make hundreds of calls at once</p>
          </div>
        </div>
        <Link
          to="/campaigns/new"
          className="flex items-center gap-1.5 px-3 py-1.5 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium rounded-lg transition"
        >
          <Plus className="w-4 h-4" />
          New Campaign
        </Link>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {error && <div className="mb-4 p-3 bg-rose-50 border border-rose-200 rounded-lg text-sm text-rose-700">{error}</div>}
        {campaigns.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mb-4">
              <Megaphone className="w-7 h-7 text-gray-400" />
            </div>
            <h2 className="text-lg font-semibold text-gray-900 mb-1">No campaigns yet</h2>
            <p className="text-sm text-gray-500 max-w-sm mb-4">
              Create a campaign, upload contacts, and start making hundreds of calls automatically.
            </p>
            <Link
              to="/campaigns/new"
              className="px-4 py-2 bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium rounded-lg transition"
            >
              Create your first campaign
            </Link>
          </div>
        ) : (
          <div className="space-y-4">
            {campaigns.map(camp => {
              const cfg = statusConfig[camp.status as keyof typeof statusConfig] || statusConfig.draft;
              return (
                <div key={camp.id} className="bg-white border border-gray-200 rounded-xl overflow-hidden hover:shadow-sm transition-shadow">
                  <div className="p-4">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <h2 className="font-semibold text-gray-900 text-sm">{camp.name}</h2>
                          <span className={`text-xs px-2 py-0.5 rounded-full ${cfg.color}`}>{cfg.label}</span>
                        </div>
                        {camp.description && (
                          <p className="text-xs text-gray-500 mt-1 truncate max-w-md">{camp.description}</p>
                        )}
                        <div className="flex items-center gap-3 mt-2 text-xs text-gray-500">
                          <span>Created {formatTime(camp.created_at)}</span>
                          {camp.employee && (
                            <>
                              <span>·</span>
                              <span className="font-medium text-gray-700">{camp.employee.name}</span>
                              {camp.employee.is_ready
                                ? <span className="text-emerald-600">● Ready</span>
                                : <span className="text-amber-600">● Not published</span>}
                            </>
                          )}
                        </div>
                      </div>
                      <div className="text-xs text-gray-500 shrink-0">
                        {camp.contact_count} contact{camp.contact_count === 1 ? '' : 's'}
                      </div>
                    </div>
                  </div>
                  <div className="px-4 pb-4 flex items-center justify-end gap-1">
                    <Link to={`/campaigns/${camp.id}`} className="p-1.5 rounded-md hover:bg-gray-100 text-gray-500 transition-colors" title="View details">
                      <ChevronRight className="w-4 h-4" />
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

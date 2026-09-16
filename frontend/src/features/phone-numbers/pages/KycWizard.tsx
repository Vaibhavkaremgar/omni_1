import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, ShieldCheck } from 'lucide-react';
import { backendJson } from '../../../services/backend/api';
import { CountryCodeSelect } from '../../../components/forms/CountryCodeSelect';

export interface KycStatus {
  region?: string;
  carrier?: string;
  status: string;
  next_step: string | null;
  can_purchase: boolean;
  kyc_required?: boolean;
  needs_contact_phone?: boolean;
}

interface KycRequirement {
  step: string;
  required: string[];
  cooldown: boolean;
  type?: string;
  redirect_url?: string;
}

interface KycRequirements {
  region: string;
  carrier: string;
  steps: KycRequirement[];
}

interface StepResult {
  status: string;
  next_step: string | null;
  can_purchase: boolean;
  message?: string;
  preview?: Record<string, unknown>;
  redirect_url?: string;
}

interface Props {
  region: string;
  carrier: string;
  phoneNumber: string;
  onComplete: () => void;
  onCancel: () => void;
}

const STEP_LABELS: Record<string, string> = {
  register: 'Register account',
  'verify-otp': 'Verify OTP',
  'verify-pan': 'PAN verification',
  'aadhaar-otp': 'Aadhaar OTP',
  'aadhaar-verify': 'Aadhaar verification',
  'verify-gst': 'GST verification',
  'skip-gst': 'Skip GST',
  preview: 'Review details',
  accept: 'Accept & complete',
};

const FIELD_LABELS: Record<string, string> = {
  name: 'Full name',
  email: 'Email address',
  phone: 'Phone number',
  pan: 'PAN number',
  aadhaar: 'Aadhaar number',
  otp: 'OTP',
  mobile_otp: 'Mobile OTP',
  gst: 'GST number',
  gstin: 'GSTIN',
};

export default function KycWizard({ region, carrier, phoneNumber, onComplete, onCancel }: Props) {
  const [status, setStatus] = useState<KycStatus | null>(null);
  const [requirements, setRequirements] = useState<KycRequirements | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [phone, setPhone] = useState('');
  const [phoneCode, setPhoneCode] = useState('+91');
  const [initDone, setInitDone] = useState(false);
  const [redirectUrl, setRedirectUrl] = useState<string | null>(null);

  const loadedRef = useRef(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      const s = await backendJson<KycStatus>(
        `/phone-numbers/kyc/status?region=${encodeURIComponent(region)}&carrier=${encodeURIComponent(carrier)}`,
      );
      setStatus(s);
      const r = await backendJson<KycRequirements>(
        `/phone-numbers/kyc/requirements?region=${encodeURIComponent(region)}&carrier=${encodeURIComponent(carrier)}`,
      );
      setRequirements(r);
      if (s.kyc_required === false || s.can_purchase || s.status === 'completed') onComplete();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to load KYC requirements right now. Please try again.');
    } finally {
      setBusy(false);
    }
  }, [region, carrier, onComplete]);

  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    load().catch(() => {/* handled inside load */});
  }, [load]);

  const initialize = async () => {
    if (!phone.trim()) return;
    setBusy(true);
    setError('');
    try {
      const s = await backendJson<KycStatus>('/phone-numbers/kyc/initialize', {
        method: 'POST',
        body: JSON.stringify({ phone: `${phoneCode}${phone}`.replace(/[ ()-]/g, '') }),
      });
      setStatus(s);
      setInitDone(true);
      if (s.can_purchase || s.status === 'completed') onComplete();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to start verification.');
    } finally {
      setBusy(false);
    }
  };

  const submitStep = async (step: string) => {
    setBusy(true);
    setError('');
    try {
      const result = await backendJson<StepResult>('/phone-numbers/kyc/step', {
        method: 'POST',
        body: JSON.stringify({
          step, region, carrier, phone_number: phoneNumber,
          values: Object.fromEntries(
            (requirements?.steps.find(item => item.step === step)?.required ?? Object.keys(fields))
              .filter(field => fields[field]?.trim())
              .map(field => [field, fields[field]]),
          ),
        }),
      });
      setFields({});
      if (result.preview) setPreview(result.preview);
      if (result.redirect_url) setRedirectUrl(result.redirect_url);
      const next: KycStatus = {
        status: result.status,
        next_step: result.next_step,
        can_purchase: result.can_purchase,
      };
      setStatus(next);
      if (result.can_purchase || result.status === 'completed') {
        onComplete();
      }
    } catch (e) {
      const providerError = e as Error & { status?: number; payload?: Partial<KycStatus> };
      if (providerError.status === 409) {
        try {
          const refreshed = await backendJson<KycStatus>(
            `/phone-numbers/kyc/status?region=${encodeURIComponent(region)}&carrier=${encodeURIComponent(carrier)}`,
          );
          setStatus(refreshed);
          setFields({});
          setError('The verification order changed. Continue with the current required step.');
        } catch {
          setError('Verification status could not be refreshed. Please try again.');
        }
        return;
      }
      setError(e instanceof Error ? e.message : 'Verification step failed. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  const resendOtp = async () => {
    setBusy(true);
    setError('');
    try {
      await backendJson('/phone-numbers/kyc/step', {
        method: 'POST',
        body: JSON.stringify({ step: 'verify-otp', region, carrier, phone_number: phoneNumber, values: { resend: true } }),
      });
    } catch (e) {
      const providerError = e as Error & { status?: number };
      if (providerError.status === 409) {
        try {
          const refreshed = await backendJson<KycStatus>(
            `/phone-numbers/kyc/status?region=${encodeURIComponent(region)}&carrier=${encodeURIComponent(carrier)}`,
          );
          setStatus(refreshed);
          setFields({});
          setError('The verification order changed. Continue with the current required step.');
        } catch {
          setError('Verification status could not be refreshed. Please try again.');
        }
      } else {
        setError('Unable to resend OTP right now.');
      }
    } finally {
      setBusy(false);
    }
  };

  if (busy && !status) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-blue-600" />
      </div>
    );
  }

  // Step 0: needs phone registration
  if (status?.needs_contact_phone && !initDone) {
    return (
      <KycCard title="Start identity verification" carrier={carrier} onCancel={onCancel}>
        <p className="text-sm text-slate-600 mb-4">
          India requires identity verification before purchasing a number. Enter your mobile number to begin.
        </p>
        <label className="block text-sm font-medium text-slate-700 mb-1">Mobile number</label>
        <div className="flex gap-2"><CountryCodeSelect value={phoneCode} onChange={setPhoneCode} disabled={busy} /><input
          type="tel"
          value={phone}
          onChange={e => setPhone(e.target.value)}
          placeholder="98765 43210"
          className="min-w-0 flex-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-blue-500"
        /></div>
        {error && <p className="text-sm text-rose-600 mb-3">{error}</p>}
        <button
          onClick={() => void initialize()}
          disabled={busy || !phone.trim()}
          className="w-full bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2.5 text-sm font-medium disabled:opacity-50"
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin inline" /> : 'Start verification'}
        </button>
      </KycCard>
    );
  }

  const currentStep = status?.next_step;
  if (redirectUrl) {
    return (
      <KycCard title="Continue verification" carrier={carrier} onCancel={onCancel}>
        <p className="text-sm text-slate-600 mb-4">Continue verification on the provider&apos;s secure page.</p>
        <a href={redirectUrl} target="_blank" rel="noreferrer" className="block w-full bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2.5 text-center text-sm font-medium">
          Continue verification
        </a>
      </KycCard>
    );
  }
  if (!currentStep) {
    return (
      <KycCard title="Verification" carrier={carrier} onCancel={onCancel}>
        <p className="text-sm text-slate-500">Checking verification status…</p>
        {error && <p className="text-sm text-rose-600 mt-2">{error}</p>}
      </KycCard>
    );
  }

  // Preview step — show redacted provider data, no sensitive storage
  if (currentStep === 'preview' && preview) {
    return (
      <KycCard title="Review your details" carrier={carrier} onCancel={onCancel}>
        <p className="text-sm text-slate-600 mb-4">Please review the information below before accepting.</p>
        <div className="bg-slate-50 rounded-lg p-4 text-xs text-slate-700 space-y-1 mb-4">
          {Object.entries(preview).map(([k, v]) => (
            <div key={k} className="flex gap-2">
              <span className="font-medium capitalize">{k.replace(/_/g, ' ')}:</span>
              <span>{String(v)}</span>
            </div>
          ))}
        </div>
        {error && <p className="text-sm text-rose-600 mb-3">{error}</p>}
        <button
          onClick={() => void submitStep('accept')}
          disabled={busy}
          className="w-full bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg py-2.5 text-sm font-medium disabled:opacity-50"
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin inline" /> : 'Accept & complete verification'}
        </button>
      </KycCard>
    );
  }

  // GST step — offer verify or skip
  if (currentStep === 'verify-gst' || currentStep === 'skip-gst') {
    return (
      <KycCard title={STEP_LABELS[currentStep] ?? currentStep} carrier={carrier} onCancel={onCancel}>
        <p className="text-sm text-slate-600 mb-4">GST registration is optional. You can provide your GSTIN or skip this step.</p>
        <label className="block text-sm font-medium text-slate-700 mb-1">GSTIN (optional)</label>
        <input
          type="text"
          value={fields['gstin'] ?? ''}
          onChange={e => setFields(f => ({ ...f, gstin: e.target.value }))}
          placeholder="22AAAAA0000A1Z5"
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        {error && <p className="text-sm text-rose-600 mb-3">{error}</p>}
        <div className="flex gap-2">
          <button
            onClick={() => void submitStep('skip-gst')}
            disabled={busy}
            className="flex-1 border border-gray-300 rounded-lg py-2.5 text-sm text-slate-600 hover:bg-gray-50 disabled:opacity-50"
          >
            Skip GST
          </button>
          <button
            onClick={() => void submitStep('verify-gst')}
            disabled={busy || !fields['gstin']?.trim()}
            className="flex-1 bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2.5 text-sm font-medium disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin inline" /> : 'Verify GST'}
          </button>
        </div>
      </KycCard>
    );
  }

  // Generic step — render required fields from requirements
  const stepDef = requirements?.steps.find(s => s.step === currentStep);
  const requiredFields = stepDef?.required ?? [];
  const isOtpStep = currentStep.includes('otp');

  if (stepDef?.redirect_url || stepDef?.type === 'redirect') {
    return (
      <KycCard title={STEP_LABELS[currentStep] ?? currentStep} carrier={carrier} onCancel={onCancel}>
        <p className="text-sm text-slate-600 mb-4">Continue verification on the provider&apos;s secure page.</p>
        <a href={stepDef.redirect_url} target="_blank" rel="noreferrer" className="block w-full bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2.5 text-center text-sm font-medium">
          Continue verification
        </a>
      </KycCard>
    );
  }

  return (
    <KycCard title={STEP_LABELS[currentStep] ?? currentStep} carrier={carrier} onCancel={onCancel}>
      <p className="text-sm text-slate-500 mb-4">
        Step: <span className="font-medium text-slate-700">{STEP_LABELS[currentStep] ?? currentStep}</span>
        {' '}· Carrier: <span className="font-medium text-slate-700">{carrier}</span>
      </p>
      {requiredFields.map(field => (
        <div key={field} className="mb-3">
          <label className="block text-sm font-medium text-slate-700 mb-1">
            {FIELD_LABELS[field] ?? field}
          </label>
          <input
            type={field.includes('otp') || field === 'pan' || field === 'aadhaar' ? 'text' : 'text'}
            value={fields[field] ?? ''}
            onChange={e => setFields(f => ({ ...f, [field]: e.target.value }))}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            autoComplete="off"
          />
        </div>
      ))}
      {error && <p className="text-sm text-rose-600 mb-3">{error}</p>}
      <div className="flex gap-2 mt-2">
        {isOtpStep && (
          <button
            onClick={() => void resendOtp()}
            disabled={busy}
            className="text-sm text-blue-600 hover:underline disabled:opacity-50"
          >
            Resend OTP
          </button>
        )}
        <button
          onClick={() => void submitStep(currentStep)}
          disabled={busy || requiredFields.some(f => !fields[f]?.trim())}
          className="flex-1 bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-2.5 text-sm font-medium disabled:opacity-50"
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin inline" /> : 'Continue'}
        </button>
      </div>
    </KycCard>
  );
}

function KycCard({ title, carrier, onCancel, children }: {
  title: string; carrier: string; onCancel: () => void; children: React.ReactNode;
}) {
  return (
    <div className="max-w-md mx-auto bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
      <div className="bg-slate-900 px-6 py-4 text-white flex items-center gap-3">
        <ShieldCheck className="w-5 h-5 text-emerald-400" />
        <div>
          <p className="text-xs text-slate-400 uppercase tracking-wider">Identity verification</p>
          <h2 className="font-semibold">{title}</h2>
        </div>
      </div>
      <div className="p-6">
        <p className="text-xs text-slate-400 mb-4">Carrier: {carrier} · Region: India</p>
        {children}
        <button onClick={onCancel} className="mt-4 w-full text-sm text-slate-400 hover:text-slate-600">
          Cancel
        </button>
      </div>
    </div>
  );
}

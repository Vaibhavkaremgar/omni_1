import { COUNTRY_CODES } from './countryCodes';

export function CountryCodeSelect({ value, onChange, disabled = false }: { value: string; onChange: (value: string) => void; disabled?: boolean }) {
  return <select aria-label="Country" value={value} onChange={e => onChange(e.target.value)} disabled={disabled} className="country-code-select w-24 shrink-0 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2.5 text-sm font-semibold text-slate-700 outline-none transition focus:border-violet-500 focus:ring-4 focus:ring-violet-100 disabled:cursor-not-allowed disabled:opacity-50"><option value="">Country</option>{COUNTRY_CODES.map(item => <option key={item.code} value={item.code}>{item.label}</option>)}</select>;
}

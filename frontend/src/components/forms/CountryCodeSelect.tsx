import { COUNTRY_CODES } from './countryCodes';

export function CountryCodeSelect({ value, onChange, disabled = false }: { value: string; onChange: (value: string) => void; disabled?: boolean }) {
  return <select aria-label="Country code" value={value} onChange={e => onChange(e.target.value)} disabled={disabled} className="w-24 shrink-0 rounded-lg border border-gray-300 bg-white px-2 py-2.5 text-sm"><option value="">Code</option>{COUNTRY_CODES.map(item => <option key={item.code} value={item.code}>{item.label}</option>)}</select>;
}

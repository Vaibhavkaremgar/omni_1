import { useEffect, useState } from 'react';
import { BadgeCheck, Check, ChevronLeft, CreditCard, MapPin, Phone, RefreshCw, Search, ShieldCheck, Sparkles, Trash2 } from 'lucide-react';
import { backendJson } from '../../../services/backend/api';
import KycWizard, { type KycStatus } from './KycWizard';

type Owned = { id: string; e164_number: string; status: string; capabilities?: { region?: string; carrier?: string } };
type Available = { phone_number: string; region: string; carrier: string; carrier_label: string | null; customer_monthly_price_inr: string | number; kyc_required: boolean | null };
type Results = { numbers: Available[] };
type Kyc = KycStatus;
type Order = { razorpay_order_id: string; amount: string | number; currency: string; key_id: string };
type Payment = { razorpay_order_id: string; razorpay_payment_id: string; razorpay_signature: string };
type Purchase = { fulfillment_status: string; message: string };
declare global { interface Window { Razorpay?: new (options: Record<string, unknown>) => { open: () => void } } }

const loadCheckout = () => new Promise<void>((resolve, reject) => { if (window.Razorpay) return resolve(); const script = document.createElement('script'); script.src = 'https://checkout.razorpay.com/v1/checkout.js'; script.onload = () => resolve(); script.onerror = () => reject(new Error('Unable to load secure payment checkout.')); document.head.appendChild(script); });
const price = (n: Available) => `₹${n.customer_monthly_price_inr}/mo`;

export default function PhoneNumbersPage() {
  const [tab, setTab] = useState<'mine' | 'buy'>('mine'); const [owned, setOwned] = useState<Owned[]>([]); const [selected, setSelected] = useState<Available | null>(null); const [message, setMessage] = useState('');
  const load = async () => { try { setOwned(await backendJson<Owned[]>('/phone-numbers')); } catch { setMessage('Unable to load your phone numbers.'); } };
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, []);
  return <div className="flex-1 flex flex-col bg-slate-50 overflow-hidden">
    <header className="bg-white border-b border-slate-200 px-6 py-5"><div className="max-w-6xl mx-auto flex items-start justify-between gap-4"><div className="flex gap-3.5"><div className="w-10 h-10 shrink-0 rounded-xl bg-blue-600 shadow-sm shadow-blue-200 grid place-items-center"><Phone className="w-5 h-5 text-white" /></div><div><h1 className="text-xl font-bold tracking-tight text-slate-900">Phone Numbers</h1><p className="mt-0.5 text-sm text-slate-500">Build a local presence with numbers for your team.</p></div></div><div className="hidden sm:flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700"><span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Service online</div></div></header>
    <div className="bg-white border-b border-slate-200 px-6"><div className="max-w-6xl mx-auto flex gap-6"><Tab active={tab === 'mine'} onClick={() => { setTab('mine'); setSelected(null); }}>My numbers <span className="ml-1.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px]">{owned.length}</span></Tab><Tab active={tab === 'buy'} onClick={() => { setTab('buy'); setSelected(null); }}>Find a number</Tab></div></div>
    <main className="flex-1 overflow-y-auto px-6 py-6"><div className="max-w-6xl mx-auto">{message && <div className="mb-5 flex items-center gap-2 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-800"><BadgeCheck className="w-4 h-4" />{message}</div>}{tab === 'mine' ? <MyNumbers numbers={owned} onRefresh={load} onMessage={setMessage} onBrowse={() => setTab('buy')} /> : selected ? <Checkout number={selected} onBack={() => setSelected(null)} onDone={() => { setSelected(null); setTab('mine'); void load(); }} /> : <Marketplace onSelect={setSelected} />}</div></main>
  </div>;
}

function Tab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) { return <button onClick={onClick} className={`relative py-3.5 text-sm font-medium transition ${active ? 'text-blue-600' : 'text-slate-500 hover:text-slate-800'}`}>{children}{active && <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-blue-600" />}</button>; }

function MyNumbers({ numbers, onRefresh, onMessage, onBrowse }: { numbers: Owned[]; onRefresh: () => Promise<void>; onMessage: (value: string) => void; onBrowse: () => void }) {
  const [syncing, setSyncing] = useState(false); const [target, setTarget] = useState<Owned | null>(null); const [releasing, setReleasing] = useState(false);
  const refresh = async () => { setSyncing(true); try { await backendJson<Owned[]>('/phone-numbers/sync', { method: 'POST' }); await onRefresh(); onMessage('Your number inventory is up to date.'); } catch { onMessage('Unable to refresh numbers right now.'); } finally { setSyncing(false); } };
  const release = async () => { if (!target) return; setReleasing(true); try { await backendJson(`/phone-numbers/${target.id}/release`, { method: 'POST' }); setTarget(null); await onRefresh(); onMessage('Number released successfully.'); } catch { setTarget(null); await onRefresh(); onMessage('Could not release the number. You can retry.'); } finally { setReleasing(false); } };
  return <section><div className="mb-6 flex items-end justify-between gap-4"><div><p className="text-xs font-semibold uppercase tracking-wider text-blue-600">Your inventory</p><h2 className="mt-1 text-lg font-semibold text-slate-900">Numbers ready for calls</h2><p className="mt-1 text-sm text-slate-500">Assign and manage the numbers connected to your workspace.</p></div><button onClick={() => void refresh()} disabled={syncing} className="inline-flex shrink-0 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 disabled:opacity-50"><RefreshCw className={`w-4 h-4 ${syncing ? 'animate-spin' : ''}`} />{syncing ? 'Refreshing' : 'Refresh'}</button></div>
    {numbers.length === 0 ? <div className="rounded-2xl border border-slate-200 bg-white px-6 py-14 text-center shadow-sm"><div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-blue-50"><Phone className="h-6 w-6 text-blue-600" /></div><h3 className="mt-5 font-semibold text-slate-900">Your number shelf is empty</h3><p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-slate-500">Choose a local number and it will appear here, ready to route calls to your team.</p><button onClick={onBrowse} className="mt-6 inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm shadow-blue-200 hover:bg-blue-700"><Search className="h-4 w-4" /> Browse available numbers</button></div> : <><div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3"><Stat label="Active numbers" value={String(numbers.filter(n => n.status !== 'released').length)} icon={<Phone className="w-4 h-4" />} /><Stat label="Coverage" value={`${new Set(numbers.map(n => n.capabilities?.region).filter(Boolean)).size || 1} region`} icon={<MapPin className="w-4 h-4" />} /><Stat label="Renewal" value="Monthly" icon={<CreditCard className="w-4 h-4" />} /></div><div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"><div className="hidden grid-cols-[1.5fr_1fr_1fr_auto] gap-4 border-b border-slate-100 bg-slate-50 px-5 py-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500 md:grid"><span>Phone number</span><span>Location & carrier</span><span>Status</span><span>Plan</span></div>{numbers.map(n => <NumberRow key={n.id} number={n} onRelease={() => setTarget(n)} />)}</div></>}
    {target && <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/40 p-4"><div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl"><div className="grid h-10 w-10 place-items-center rounded-xl bg-rose-50"><Trash2 className="w-5 h-5 text-rose-600" /></div><h3 className="mt-4 text-lg font-semibold text-slate-900">Release this number?</h3><p className="mt-1 font-mono text-sm font-medium text-slate-800">{target.e164_number}</p><p className="mt-3 text-sm leading-6 text-slate-500">This stops future renewals and removes the number from your workspace. It may not be recoverable.</p><div className="mt-6 flex justify-end gap-3"><button disabled={releasing} onClick={() => setTarget(null)} className="rounded-lg px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100">Cancel</button><button disabled={releasing} onClick={() => void release()} className="rounded-lg bg-rose-600 px-4 py-2 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-50">{releasing ? 'Releasing…' : 'Release number'}</button></div></div></div>}
  </section>;
}

function Stat({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) { return <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"><div className="flex items-center gap-2 text-xs font-medium text-slate-500"><span className="text-blue-600">{icon}</span>{label}</div><div className="mt-2 text-lg font-semibold text-slate-900">{value}</div></div>; }
function NumberRow({ number, onRelease }: { number: Owned; onRelease: () => void }) { const released = number.status === 'released'; const failed = number.status === 'release_failed'; return <div className="grid gap-3 px-5 py-4 md:grid-cols-[1.5fr_1fr_1fr_auto] md:items-center md:gap-4"><div className="flex items-center gap-3"><div className="grid h-9 w-9 place-items-center rounded-lg bg-blue-50"><Phone className="h-4 w-4 text-blue-600" /></div><div><div className="font-mono text-sm font-semibold text-slate-900">{number.e164_number}</div><div className="mt-0.5 text-xs text-slate-500">Voice calling enabled</div></div></div><div className="text-sm text-slate-600"><span className="md:hidden text-xs text-slate-400">Network · </span>{number.capabilities?.region || '—'} <span className="text-slate-300">/</span> {number.capabilities?.carrier || '—'}</div><div><span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium capitalize ${released ? 'bg-slate-100 text-slate-600' : failed ? 'bg-rose-50 text-rose-700' : 'bg-emerald-50 text-emerald-700'}`}><span className={`h-1.5 w-1.5 rounded-full ${released ? 'bg-slate-400' : failed ? 'bg-rose-500' : 'bg-emerald-500'}`} />{number.status.replace(/_/g, ' ')}</span></div><div className="flex items-center justify-between gap-4"><span className="text-sm font-medium text-slate-700">{released ? 'No renewal' : '₹650/mo'}</span>{!released && (number.status === 'release_pending' ? <span className="text-xs text-slate-400">Releasing…</span> : <button onClick={onRelease} className="text-xs font-medium text-rose-600 hover:text-rose-700">{failed ? 'Retry release' : 'Release'}</button>)}</div></div>; }

function Marketplace({ onSelect }: { onSelect: (number: Available) => void }) {
  const [region, setRegion] = useState('IN'); const [carrier, setCarrier] = useState('carrier-1'); const [pattern, setPattern] = useState(''); const [result, setResult] = useState<Results | null>(null); const [error, setError] = useState(''); const [searching, setSearching] = useState(false);
  const search = async () => { setSearching(true); setError(''); try { const q = new URLSearchParams({ region, carrier, page: '1', limit: '20' }); if (pattern) q.set('pattern', pattern); setResult(await backendJson<Results>(`/phone-numbers/marketplace?${q}`)); } catch { setError('Live availability could not be loaded. Please try again.'); } finally { setSearching(false); } };
  return <section><div className="rounded-2xl bg-gradient-to-br from-blue-700 to-indigo-700 p-6 text-white shadow-lg shadow-blue-200"><div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-start"><div><div className="flex items-center gap-2 text-sm font-medium text-blue-100"><Sparkles className="h-4 w-4" /> Live number marketplace</div><h2 className="mt-2 text-xl font-semibold">Find a number that feels familiar</h2><p className="mt-1 max-w-lg text-sm leading-6 text-blue-100">Search live inventory by region, provider, or a memorable number pattern.</p></div><div className="flex items-center gap-2 rounded-xl bg-white/10 px-3 py-2 text-xs text-blue-50"><ShieldCheck className="h-4 w-4" /> Secure checkout</div></div><div className="mt-6 grid gap-3 rounded-xl bg-white p-3 text-slate-900 md:grid-cols-[1fr_1fr_1.1fr_auto]"><label className="px-1"><span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-slate-500">Region</span><select value={region} onChange={e => { setRegion(e.target.value); setCarrier(e.target.value === 'IN' ? 'carrier-1' : 'carrier-us'); }} className="w-full bg-transparent text-sm font-medium outline-none"><option value="IN">India</option><option value="US">United States</option></select></label><label className="border-t border-slate-100 px-1 pt-3 md:border-l md:border-t-0 md:px-4 md:pt-0"><span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-slate-500">Provider</span><select value={carrier} onChange={e => setCarrier(e.target.value)} className="w-full bg-transparent text-sm font-medium outline-none"><option value="carrier-1">Carrier 1</option><option value="carrier-2-new">Carrier 2</option><option value="carrier-us">US carrier</option></select></label><label className="border-t border-slate-100 px-1 pt-3 md:border-l md:border-t-0 md:px-4 md:pt-0"><span className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-slate-500">Contains digits</span><input value={pattern} onChange={e => setPattern(e.target.value.replace(/\D/g, ''))} className="w-full text-sm outline-none placeholder:text-slate-400" placeholder="e.g. 1234" /></label><button onClick={() => void search()} disabled={searching} className="mt-2 inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60 md:mt-4">{searching ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}{searching ? 'Searching' : 'Search'}</button></div></div>
    {error && <p className="mt-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p>}
    {!result && !error && <div className="mt-6 grid gap-3 sm:grid-cols-3"><Info icon={<MapPin />} title="Local presence" text="Choose the market you want to serve." /><Info icon={<BadgeCheck />} title="Verified inventory" text="Availability is checked in real time." /><Info icon={<CreditCard />} title="Simple billing" text="Clear monthly pricing before checkout." /></div>}
    {result && <div className="mt-6"><div className="mb-3 flex items-center justify-between"><div><h3 className="font-semibold text-slate-900">Available numbers</h3><p className="mt-0.5 text-sm text-slate-500">{result.numbers.length} matches from live inventory</p></div></div>{result.numbers.length === 0 ? <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-500">No matches for this search. Try a different provider or a broader pattern.</div> : <div className="grid gap-3 lg:grid-cols-2">{result.numbers.map(n => <article key={n.phone_number} className="group rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-md"><div className="flex items-start justify-between gap-4"><div><div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50"><Phone className="h-4 w-4 text-blue-600" /></div><h4 className="mt-4 font-mono text-base font-semibold text-slate-900">{n.phone_number}</h4><p className="mt-1 flex items-center gap-1 text-xs text-slate-500"><MapPin className="h-3 w-3" /> {n.region} · {n.carrier_label || n.carrier}</p>{n.kyc_required && <span className="mt-3 inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700"><BadgeCheck className="h-3 w-3" /> KYC required</span>}</div><div className="text-right"><div className="text-base font-semibold text-slate-900">{price(n)}</div><div className="mt-0.5 text-xs text-slate-400">billed monthly</div></div></div><button onClick={() => onSelect(n)} className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg border border-blue-200 bg-blue-50 py-2.5 text-sm font-medium text-blue-700 transition group-hover:bg-blue-600 group-hover:text-white"><Check className="h-4 w-4" /> Choose this number</button></article>)}</div>}</div>}
  </section>;
}

function Info({ icon, title, text }: { icon: React.ReactNode; title: string; text: string }) { return <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"><div className="text-blue-600 [&>svg]:h-5 [&>svg]:w-5">{icon}</div><h3 className="mt-3 text-sm font-semibold text-slate-800">{title}</h3><p className="mt-1 text-xs leading-5 text-slate-500">{text}</p></div>; }

function Checkout({ number, onBack, onDone }: { number: Available; onBack: () => void; onDone: () => void }) {
  const [kyc, setKyc] = useState<Kyc | null>(null);
  const [showKyc, setShowKyc] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState('');

  useEffect(() => {
    if (number.kyc_required) {
      backendJson<Kyc>(`/phone-numbers/kyc/status?region=${encodeURIComponent(number.region)}&carrier=${encodeURIComponent(number.carrier)}`)
        .then(s => {
          setKyc(s);
          if (!s.can_purchase && s.status !== 'completed') setShowKyc(true);
        })
        .catch(() => setShowKyc(true));
    }
  }, [number.carrier, number.kyc_required, number.phone_number, number.region]);

  const pay = async () => {
    setBusy(true);
    try {
      const order = await backendJson<Order>('/phone-numbers/purchase/order', {
        method: 'POST',
        body: JSON.stringify({ phone_number: number.phone_number, region: number.region, carrier: number.carrier }),
      });
      await loadCheckout();
      if (!window.Razorpay) throw new Error();
      new window.Razorpay({
        key: order.key_id,
        amount: Math.round(Number(order.amount) * 100),
        currency: order.currency,
        order_id: order.razorpay_order_id,
        name: 'Phone number',
        handler: async (payment: Payment) => {
          try {
            const status = await backendJson<Purchase>('/phone-numbers/purchase/verify', {
              method: 'POST',
              body: JSON.stringify(payment),
            });
            setResult(status.message);
            if (status.fulfillment_status === 'fulfilled') onDone();
          } catch {
            setResult('Payment received. Activating your number is being verified safely.');
          } finally { setBusy(false); }
        },
        modal: { ondismiss: () => setBusy(false) },
      }).open();
    } catch {
      setResult('Unable to start payment.');
      setBusy(false);
    }
  };

  if (showKyc) {
    return (
      <section className="max-w-2xl">
        <button onClick={onBack} className="mb-5 inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 hover:text-slate-800">
          <ChevronLeft className="h-4 w-4" /> Back to results
        </button>
        <KycWizard
          region={number.region}
          carrier={number.carrier}
          phoneNumber={number.phone_number}
          onComplete={() => { setShowKyc(false); setKyc({ status: 'completed', next_step: null, can_purchase: true }); }}
          onCancel={onBack}
        />
      </section>
    );
  }

  const canPay = !number.kyc_required || (kyc?.can_purchase || kyc?.status === 'completed');

  return (
    <section className="max-w-2xl">
      <button onClick={onBack} className="mb-5 inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 hover:text-slate-800">
        <ChevronLeft className="h-4 w-4" /> Back to results
      </button>
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="bg-slate-900 px-6 py-5 text-white">
          <p className="text-xs font-medium uppercase tracking-wider text-slate-400">Selected number</p>
          <h2 className="mt-2 font-mono text-2xl font-semibold">{number.phone_number}</h2>
          <p className="mt-2 text-sm text-slate-300">{number.region} · {number.carrier_label || number.carrier}</p>
        </div>
        <div className="p-6">
          <div className="flex items-center justify-between rounded-xl bg-slate-50 p-4">
            <span className="text-sm text-slate-600">Monthly number plan</span>
            <span className="text-lg font-semibold text-slate-900">{price(number)}</span>
          </div>
          {result && <p className="mt-4 rounded-xl bg-blue-50 p-3 text-sm text-blue-800">{result}</p>}
          <div className="mt-6 flex items-start gap-3 text-sm text-slate-500">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
            <p>Your payment is handled through our secure checkout. You can manage or release this number any time.</p>
          </div>
          {canPay ? (
            <button
              disabled={busy}
              onClick={() => void pay()}
              className="mt-6 w-full rounded-lg bg-blue-600 px-4 py-3 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {busy ? 'Opening secure checkout…' : `Pay ${price(number)}`}
            </button>
          ) : (
            <button
              onClick={() => setShowKyc(true)}
              className="mt-6 w-full rounded-lg bg-amber-600 px-4 py-3 text-sm font-medium text-white hover:bg-amber-700"
            >
              Complete identity verification
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

import { useEffect, useState } from 'react'
import { accessToken, apiUrl } from './auth'

type Entry = { id: string; name: string; kind: 'income' | 'expense'; amount_cents: number; start_date: string; end_date: string | null; recurrence: 'once' | 'weekly' | 'biweekly' | 'monthly' | 'annual'; effective_date: string; retired_on: string | null; history: { effective_date: string; entry: { name: string; amount_cents: number } }[] }
type Draft = Omit<Entry, 'id' | 'amount_cents' | 'effective_date' | 'history' | 'retired_on'> & { amount: string }
const empty: Draft = { name: '', kind: 'expense', amount: '', start_date: '', end_date: null, recurrence: 'monthly' }

async function api(path: string, init: RequestInit = {}) {
  const response = await fetch(`${apiUrl}${path}`, { ...init, cache: 'no-store', headers: { Authorization: `Bearer ${accessToken()}`, 'Content-Type': 'application/json' } })
  if (!response.ok) throw new Error('Request failed')
  return response.status === 204 ? null : response.json()
}

export default function Expected() {
  const [entries, setEntries] = useState<Entry[]>([])
  const [draft, setDraft] = useState<Draft>(empty)
  const [effectiveDate, setEffectiveDate] = useState('')
  const [retiring, setRetiring] = useState<string | null>(null)
  const [retireDate, setRetireDate] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  async function load() { const data = await api('/v1/expected'); setEntries(data.entries) }
  useEffect(() => { void load().catch(() => setMessage('Unable to load expected entries.')) }, [])
  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!/^\d{1,6}(\.\d{1,2})?$/.test(draft.amount)) { setMessage('Enter a positive amount with at most two decimal places.'); return }
    const [dollars, cents = ''] = draft.amount.split('.')
    const amount_cents = Number(dollars) * 100 + Number(cents.padEnd(2, '0'))
    if (!amount_cents) { setMessage('Enter a positive amount.'); return }
    const { amount: _amount, ...rest } = draft
    try {
      await api(editing ? `/v1/expected/${editing}` : '/v1/expected', { method: editing ? 'PUT' : 'POST', body: JSON.stringify({ ...rest, ...(editing ? { effective_date: effectiveDate } : {}), end_date: draft.recurrence === 'once' ? null : draft.end_date || null, amount_cents }) })
      await load(); setDraft(empty); setEditing(null); setMessage('Expected entry saved.')
    } catch { setMessage('Unable to save expected entry. Check the dates and try again.') }
  }
  async function retire(event: React.FormEvent, id: string) {
    event.preventDefault()
    try {
      await api(`/v1/expected/${id}?effective_date=${encodeURIComponent(retireDate)}`, { method: 'DELETE' })
      await load(); setRetiring(null); setRetireDate('')
      if (editing === id) { setEditing(null); setDraft(empty) }
      setMessage('Expected entry retired; its history is retained.')
    } catch { setMessage('Unable to retire entry. Choose a date on or after its latest version and try again.') }
  }
  return <section className="overview" aria-label="Expected income and expenses">
    <div className="section-head"><div><p className="eyebrow">FORECAST SETUP</p><h2>Expected income and expenses</h2></div></div>
    <p>Set up salary, bills and other expected cash flows. Forecast balances are not available yet. Match transactions manually or create a forecast rule in the transactions section. Retiring an entry preserves its history and stops occurrences on the chosen date.</p>
    <form onSubmit={event => void save(event)}>
      <p><label>Name <input required maxLength={80} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label></p>
      <p><label>Type <select value={draft.kind} onChange={e => setDraft({ ...draft, kind: e.target.value as Draft['kind'] })}><option value="income">Income</option><option value="expense">Expense</option></select></label> <label>Amount ($) <input required inputMode="decimal" value={draft.amount} onChange={e => setDraft({ ...draft, amount: e.target.value })} /></label></p>
      <p><label>First date <input required type="date" value={draft.start_date} onChange={e => setDraft({ ...draft, start_date: e.target.value })} /></label> <label>Repeats <select value={draft.recurrence} onChange={e => setDraft({ ...draft, recurrence: e.target.value as Draft['recurrence'] })}><option value="once">Once</option><option value="weekly">Weekly</option><option value="biweekly">Every two weeks</option><option value="monthly">Monthly</option><option value="annual">Annually</option></select></label></p>
      {draft.recurrence !== 'once' && <p><label>Last date (optional) <input type="date" min={draft.start_date} value={draft.end_date ?? ''} onChange={e => setDraft({ ...draft, end_date: e.target.value || null })} /></label></p>}
      {editing && <p><label>Changes take effect on <input required type="date" value={effectiveDate} onChange={e => setEffectiveDate(e.target.value)} /></label> Earlier scheduled occurrences retain their previous values.</p>}
      <button type="submit">{editing ? 'Save changes' : 'Add expected entry'}</button> {editing && <button type="button" onClick={() => { setEditing(null); setDraft(empty) }}>Cancel</button>}
    </form>
    {entries.map(entry => <div key={entry.id}><p>{entry.name} · {entry.kind} · ${(entry.amount_cents / 100).toFixed(2)} · {entry.recurrence} from {entry.start_date}{entry.end_date && ` through ${entry.end_date}`} · current version from {entry.effective_date}{entry.history.length > 0 && ` · ${entry.history.length} earlier version(s)`}{entry.retired_on && ` · retired from ${entry.retired_on}`}{!entry.retired_on && <> <button onClick={() => { setEditing(entry.id); setEffectiveDate(''); setDraft({ name: entry.name, kind: entry.kind, start_date: entry.start_date, end_date: entry.end_date, recurrence: entry.recurrence, amount: (entry.amount_cents / 100).toFixed(2) }) }}>Edit</button> <button onClick={() => { setRetiring(entry.id); setRetireDate('') }}>Retire</button></>}</p>
      {retiring === entry.id && !entry.retired_on && <form onSubmit={event => void retire(event, entry.id)}><label>Stop occurrences on and after <input required type="date" min={entry.effective_date} value={retireDate} onChange={e => setRetireDate(e.target.value)} /></label> <button type="submit">Retire entry</button> <button type="button" onClick={() => setRetiring(null)}>Cancel</button></form>}
    </div>)}
    <p role="status">{message}</p>
  </section>
}

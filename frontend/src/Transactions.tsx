import { useEffect, useState } from 'react'
import { accessToken, apiUrl } from './auth'

type Category = { id: string; name: string }
type Choice = { kind?: string; category_id?: string }
type Transaction = { transaction_id: string; pending_transaction_id?: string; account_id?: string; name?: string; merchant_name?: string; date: string; amount: number; pending?: boolean; classification: Choice }
type Expected = { id: string; name: string }
type Match = { transaction_id: string; entry_id: string; occurrence_date: string; automatic?: boolean }
type Total = { planned_cents: number; spent_cents: number; remaining_cents: number }

async function api(path: string, init: RequestInit = {}) {
  const response = await fetch(`${apiUrl}${path}`, { ...init, cache: 'no-store',
    headers: { Authorization: `Bearer ${accessToken()}`, 'Content-Type': 'application/json' } })
  if (!response.ok) throw new Error('Request failed')
  return response.status === 204 ? null : response.json()
}

const money = (cents: number) => (cents / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD' })

function TransactionRow({ transaction, categories, entries, match, onSaved }: { transaction: Transaction; categories: Category[]; entries: Expected[]; match?: Match; onSaved: () => void }) {
  const [kind, setKind] = useState(transaction.classification.kind || '')
  const [category, setCategory] = useState(transaction.classification.category_id || '')
  const [message, setMessage] = useState('')
  const [entryId, setEntryId] = useState('')
  const [showRule, setShowRule] = useState(false)
  const [windowDays, setWindowDays] = useState('5')
  const [tolerance, setTolerance] = useState('')
  const [occurrenceDate, setOccurrenceDate] = useState(transaction.date)
  const needsCategory = kind === 'spending' || kind === 'refund'
  async function save() {
    try {
      await api(`/v1/transactions/${encodeURIComponent(transaction.transaction_id)}/classification`, {
        method: 'PUT', body: JSON.stringify({ kind, category_id: needsCategory ? category : null }),
      })
      setMessage('Saved'); onSaved()
    } catch { setMessage('Could not save classification.') }
  }
  async function matchTransaction() {
    try { await api('/v1/forecast-matches', { method: 'POST', body: JSON.stringify({ transaction_id: transaction.transaction_id, entry_id: entryId, occurrence_date: occurrenceDate }) }); setMessage('Forecast match saved.'); onSaved() }
    catch { setMessage('Could not match. Check the occurrence date, direction, and existing matches.') }
  }
  async function createRule(event: React.FormEvent) {
    event.preventDefault()
    if (!match) return
    const value = transaction.merchant_name || transaction.name
    if (!value) { setMessage('This transaction has no name for a rule.'); return }
    try {
      await api('/v1/category-rules', { method: 'POST', body: JSON.stringify({
        field: transaction.merchant_name ? 'merchant_name' : 'name', operator: 'exact', value,
        direction: transaction.amount < 0 ? 'incoming' : 'outgoing', account_id: transaction.account_id || null,
        forecast: { entry_id: match.entry_id, window_days: Number(windowDays), amount_tolerance_cents: tolerance === '' ? null : Number(tolerance) },
      }) })
      setShowRule(false); setMessage('Forecast rule created for future imports. Refresh the page to manage it in Transaction rules.')
    } catch { setMessage('Could not create forecast rule. Check your settings.') }
  }
  async function unmatch() {
    if (!match) return
    try { await api(`/v1/forecast-matches/${encodeURIComponent(match.transaction_id)}`, { method: 'DELETE' }); setMessage('Forecast match removed.'); onSaved() }
    catch { setMessage('Could not remove forecast match.') }
  }
  return <div>
    <p><strong>{transaction.merchant_name || transaction.name || 'Transaction'}</strong> · {transaction.date} · {money(Math.round(transaction.amount * 100))}{transaction.pending && ' · Pending'}</p>
    <label>Type <select value={kind} onChange={event => setKind(event.target.value)}>
      <option value="">Unclassified</option><option value="spending">Spending</option><option value="refund">Refund</option>
      <option value="transfer">Transfer</option><option value="card_payment">Card payment</option><option value="income">Income</option><option value="excluded">Excluded</option>
    </select></label>{' '}
    {needsCategory && <label>Category <select value={category} onChange={event => setCategory(event.target.value)}>
      <option value="">Select a category</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
    </select></label>}{' '}
    <button disabled={!kind || (needsCategory && !category)} onClick={() => void save()}>Save</button> <span role="status">{message}</span>
    {match ? <p>Matched to {entries.find(e => e.id === match.entry_id)?.name || 'expected entry'} · {match.occurrence_date}{match.automatic && ' · automatic'} <button onClick={() => void unmatch()}>Unmatch</button> {!match.automatic && <button onClick={() => setShowRule(!showRule)}>Create a rule for future transactions</button>}</p> : <p><label>Expected entry <select value={entryId} onChange={e => setEntryId(e.target.value)}><option value="">Select</option>{entries.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label> <label>Occurrence date <input type="date" value={occurrenceDate} onChange={e => setOccurrenceDate(e.target.value)} /></label> <button disabled={!entryId || !occurrenceDate} onClick={() => void matchTransaction()}>Match to forecast</button></p>}
    {match && showRule && <form onSubmit={event => void createRule(event)}><p>Match future {transaction.merchant_name ? 'merchant' : 'transaction'} names exactly: {transaction.merchant_name || transaction.name}. The rule uses this account when available.</p><label>Days before or after <input required type="number" min="0" max="31" value={windowDays} onChange={e => setWindowDays(e.target.value)} /></label> <label>Amount tolerance in cents (blank allows any amount) <input type="number" min="0" max="100000000" value={tolerance} onChange={e => setTolerance(e.target.value)} /></label> <button type="submit">Create forecast rule</button></form>}
  </div>
}

export default function Transactions() {
  const [month, setMonth] = useState(() => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit' }).format(new Date()))
  const [rows, setRows] = useState<Transaction[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [entries, setEntries] = useState<Expected[]>([])
  const [matches, setMatches] = useState<Match[]>([])
  const [totals, setTotals] = useState<Record<string, Total>>({})
  const [unclassified, setUnclassified] = useState(0)
  const [version, setVersion] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let cancelled = false
    setLoading(true); setRows([]); setTotals({}); setError('')
    Promise.all([api('/v1/categories'), api(`/v1/transactions?month=${month}`), api(`/v1/dashboard?month=${month}`), api('/v1/expected'), api('/v1/forecast-matches')])
      .then(([cats, transactions, dashboard, expected, forecastMatches]) => {
        if (cancelled) return
        setEntries(expected.entries); setMatches(forecastMatches.matches); setCategories(cats.categories); setRows(transactions.transactions); setTotals(dashboard.categories)
        setUnclassified(dashboard.unclassified_count); setError('')
      }).catch(() => { if (!cancelled) setError('Could not load transactions or spending.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [month, version])
  return <section className="overview" aria-label="Transactions and spending">
    <div className="section-head"><h2>Transactions and spending</h2><label>Month <input type="month" value={month} onChange={event => setMonth(event.target.value)} /></label></div>
    <p>Pending transactions are included. Unclassified transactions are not included in spending.</p>
    <button onClick={() => setVersion(v => v + 1)}>Refresh transactions and categories</button>
    <p role="status">{loading ? 'Loading…' : error || `${unclassified} transaction(s) need classification or have an incompatible amount.`}</p>
    {!loading && !error && categories.map(c => <p key={c.id}><strong>{c.name}</strong>: planned {money(totals[c.id]?.planned_cents || 0)}, spent {money(totals[c.id]?.spent_cents || 0)}, remaining {money(totals[c.id]?.remaining_cents || 0)}</p>)}
    {rows.map(t => <TransactionRow key={t.transaction_id} transaction={t} categories={categories} entries={entries} match={matches.find(m => m.transaction_id === t.transaction_id || m.transaction_id === t.pending_transaction_id)} onSaved={() => setVersion(v => v + 1)} />)}
  </section>
}

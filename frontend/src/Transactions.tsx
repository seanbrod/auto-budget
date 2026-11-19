import { useEffect, useState } from 'react'
import { accessToken, apiUrl } from './auth'

type Category = { id: string; name: string }
type Choice = { kind?: string; category_id?: string }
type Transaction = { transaction_id: string; name?: string; merchant_name?: string; date: string; amount: number; pending?: boolean; classification: Choice }
type Total = { planned_cents: number; spent_cents: number; remaining_cents: number }

async function api(path: string, init: RequestInit = {}) {
  const response = await fetch(`${apiUrl}${path}`, { ...init, cache: 'no-store',
    headers: { Authorization: `Bearer ${accessToken()}`, 'Content-Type': 'application/json' } })
  if (!response.ok) throw new Error('Request failed')
  return response.json()
}

const money = (cents: number) => (cents / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD' })

function TransactionRow({ transaction, categories, onSaved }: { transaction: Transaction; categories: Category[]; onSaved: () => void }) {
  const [kind, setKind] = useState(transaction.classification.kind || '')
  const [category, setCategory] = useState(transaction.classification.category_id || '')
  const [message, setMessage] = useState('')
  const needsCategory = kind === 'spending' || kind === 'refund'
  async function save() {
    try {
      await api(`/v1/transactions/${encodeURIComponent(transaction.transaction_id)}/classification`, {
        method: 'PUT', body: JSON.stringify({ kind, category_id: needsCategory ? category : null }),
      })
      setMessage('Saved'); onSaved()
    } catch { setMessage('Could not save classification.') }
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
  </div>
}

export default function Transactions() {
  const [month, setMonth] = useState(() => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit' }).format(new Date()))
  const [rows, setRows] = useState<Transaction[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [totals, setTotals] = useState<Record<string, Total>>({})
  const [unclassified, setUnclassified] = useState(0)
  const [version, setVersion] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let cancelled = false
    setLoading(true); setRows([]); setTotals({}); setError('')
    Promise.all([api('/v1/categories'), api(`/v1/transactions?month=${month}`), api(`/v1/dashboard?month=${month}`)])
      .then(([cats, transactions, dashboard]) => {
        if (cancelled) return
        setCategories(cats.categories); setRows(transactions.transactions); setTotals(dashboard.categories)
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
    {rows.map(t => <TransactionRow key={t.transaction_id} transaction={t} categories={categories} onSaved={() => setVersion(v => v + 1)} />)}
  </section>
}

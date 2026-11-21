import { useEffect, useState } from 'react'
import { accessToken, apiUrl } from './auth'

type Category = { id: string; name: string }
type Rule = { id: string; field: string; operator: string; value: string; direction: string; priority: number; enabled: boolean; account_id?: string; classification?: { kind: string; category_id?: string }; forecast?: { entry_id: string; window_days: number; amount_tolerance_cents?: number } }
type Preview = { count: number; truncated: boolean; preview_token: string; transactions: { transaction_id: string; name?: string; date?: string; amount?: number; pending?: boolean }[] }

async function api(path: string, init: RequestInit = {}) {
  const response = await fetch(`${apiUrl}${path}`, { ...init, cache: 'no-store', headers: {
    Authorization: `Bearer ${accessToken()}`, 'Content-Type': 'application/json',
  } })
  if (!response.ok) throw new Error('Request failed')
  return response.json()
}

export default function Rules() {
  const [rules, setRules] = useState<Rule[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [entries, setEntries] = useState<Category[]>([])
  const [forecastId, setForecastId] = useState('')
  const [windowDays, setWindowDays] = useState('5')
  const [tolerance, setTolerance] = useState('')
  const [field, setField] = useState('name')
  const [operator, setOperator] = useState('contains')
  const [value, setValue] = useState('')
  const [direction, setDirection] = useState('outgoing')
  const [kind, setKind] = useState('spending')
  const [category, setCategory] = useState('')
  const [priority, setPriority] = useState('100')
  const [editing, setEditing] = useState<Rule | null>(null)
  const [enabled, setEnabled] = useState(true)
  const [message, setMessage] = useState('')
  const [preview, setPreview] = useState<{ id: string; data: Preview } | null>(null)
  const [version, setVersion] = useState(0)
  const needsCategory = kind === 'spending' || kind === 'refund'

  useEffect(() => {
    let cancelled = false
    Promise.all([api('/v1/category-rules'), api('/v1/categories'), api('/v1/expected')]).then(([r, c, e]) => {
      if (!cancelled) { setRules(r.rules); setCategories(c.categories); setEntries(e.entries) }
    }).catch(() => { if (!cancelled) setMessage('Could not load rules and categories.') })
    return () => { cancelled = true }
  }, [version])

  async function create(event: React.FormEvent) {
    event.preventDefault()
    try {
      await api(editing ? `/v1/category-rules/${editing.id}` : '/v1/category-rules', { method: editing ? 'PUT' : 'POST', body: JSON.stringify({ field, operator, value, direction,
        account_id: editing?.account_id ?? null, priority: Number(priority), enabled, classification: kind ? { kind, category_id: needsCategory ? category : null } : null, forecast: forecastId ? { entry_id: forecastId, window_days: Number(windowDays), amount_tolerance_cents: tolerance === '' ? null : Number(tolerance) } : null }) })
      setValue(''); setEditing(null); setEnabled(true); setPreview(null); setMessage('Rule saved for new transactions. Preview before applying it to existing transactions.')
      setVersion(v => v + 1)
    } catch { setMessage('Could not create rule. Check the values and try again.') }
  }
  async function inspect(id: string) {
    try { setPreview({ id, data: await api(`/v1/category-rules/${id}/preview`, { method: 'POST' }) }); setMessage('') }
    catch { setMessage('Could not preview rule.') }
  }
  async function apply(id: string) {
    try {
      const result = await api(`/v1/category-rules/${id}/apply`, { method: 'POST', body: JSON.stringify({ preview_token: preview?.data.preview_token }) })
      setPreview(null); setMessage(`Applied to ${result.applied} transaction(s); ${result.skipped} skipped. ${result.remaining} remain; preview again to continue.`)
    } catch { setPreview(null); setMessage('Could not apply the rule. Refresh the preview and try again.') }
  }
  async function remove(id: string) {
    try { await api(`/v1/category-rules/${id}`, { method: 'DELETE' }); setPreview(null); setVersion(v => v + 1); setMessage('Rule removed for future imports.') }
    catch { setMessage('Could not remove rule.') }
  }
  function edit(rule: Rule) {
    setEditing(rule); setField(rule.field); setOperator(rule.operator); setValue(rule.value); setDirection(rule.direction)
    setKind(rule.classification?.kind || ''); setCategory(rule.classification?.category_id || ''); setForecastId(rule.forecast?.entry_id || ''); setWindowDays(String(rule.forecast?.window_days ?? 5)); setTolerance(rule.forecast?.amount_tolerance_cents == null ? '' : String(rule.forecast.amount_tolerance_cents)); setPriority(String(rule.priority))
    setEnabled(rule.enabled); setPreview(null)
  }

  return <section className="overview" aria-label="Transaction rules">
    <h2>Transaction rules</h2>
    <p>Rules can classify or match newly imported transactions to forecast entries. Lower priority numbers win for each action. Manual choices take priority; classification can be previewed and applied to existing transactions.</p>
    <form onSubmit={event => void create(event)}>
      <label>Match <select value={field} onChange={e => setField(e.target.value)}><option value="name">Transaction name</option><option value="merchant_name">Merchant name</option></select></label>{' '}
      <label>Operator <select value={operator} onChange={e => setOperator(e.target.value)}><option value="contains">Contains</option><option value="exact">Exactly matches</option><option value="regex">Regular expression</option></select></label>{' '}
      <label>Text <input value={value} onChange={e => setValue(e.target.value)} maxLength={100} required /></label>{' '}
      <label>Direction <select value={direction} onChange={e => setDirection(e.target.value)}><option value="outgoing">Outgoing</option><option value="incoming">Incoming</option><option value="either">Either</option></select></label>{' '}
      <label>Type <select value={kind} onChange={e => setKind(e.target.value)}><option value="">No classification</option><option value="spending">Spending</option><option value="refund">Refund</option><option value="transfer">Transfer</option><option value="card_payment">Card payment</option><option value="income">Income</option><option value="excluded">Excluded</option></select></label>{' '}
      {needsCategory && <label>Category <select value={category} onChange={e => setCategory(e.target.value)} required><option value="">Select</option>{categories.map(c => <option value={c.id} key={c.id}>{c.name}</option>)}</select></label>}{' '}
      <label>Forecast entry <select value={forecastId} onChange={e => setForecastId(e.target.value)}><option value="">None</option>{entries.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label>{' '}
      {forecastId && <><label>Days before or after <input type="number" min="0" max="31" required value={windowDays} onChange={e => setWindowDays(e.target.value)} /></label>{' '}<label>Amount tolerance (cents; blank allows any amount) <input type="number" min="0" max="100000000" value={tolerance} onChange={e => setTolerance(e.target.value)} /></label>{' '}</>}
      <label>Priority <input type="number" min="0" max="10000" value={priority} onChange={e => setPriority(e.target.value)} required /></label>{' '}
      <label><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /> Enabled</label>{' '}
      <button type="submit">{editing ? 'Save rule' : 'Create rule'}</button>{' '}
      {editing && <button type="button" onClick={() => { setEditing(null); setValue(''); setEnabled(true) }}>Cancel edit</button>}
    </form>
    <p role="status">{message}</p>
    {rules.map(rule => <div key={rule.id}><p>{rule.field === 'name' ? 'Transaction name' : 'Merchant name'} {rule.operator} “{rule.value}” · {rule.direction} · {rule.classification?.kind || 'no classification'}{rule.forecast && ` · forecast: ${entries.find(e => e.id === rule.forecast?.entry_id)?.name || 'expected entry'} (±${rule.forecast.window_days} days)`} · priority {rule.priority}{!rule.enabled && ' · disabled'}</p>
      <button onClick={() => edit(rule)}>Edit rule</button>{' '}{rule.classification && <button onClick={() => void inspect(rule.id)}>Preview existing</button>}{' '}<button onClick={() => void remove(rule.id)}>Remove rule</button>
      {preview?.id === rule.id && <div><p>{preview.data.count} matching transaction(s) without manual choices{preview.data.truncated ? ' (first 25 shown; preview again after applying)' : ''}.</p>
        <ul>{preview.data.transactions.map(t => <li key={t.transaction_id}>{t.date} · {t.name} · {t.amount?.toLocaleString('en-US', { style: 'currency', currency: 'USD' })}{t.pending && ' · Pending'}</li>)}</ul>
        <button disabled={preview.data.count === 0} onClick={() => void apply(rule.id)}>Apply to these existing transactions</button></div>}
    </div>)}
  </section>
}

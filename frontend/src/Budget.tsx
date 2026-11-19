import { useEffect, useState } from 'react'
import { accessToken, apiUrl } from './auth'

type Category = { id: string; name: string }

async function api(path: string, init: RequestInit = {}) {
  const response = await fetch(`${apiUrl}${path}`, {
    ...init, cache: 'no-store', headers: { Authorization: `Bearer ${accessToken()}`, 'Content-Type': 'application/json' },
  })
  if (!response.ok) throw new Error('Unable to save or load the budget. Please try again.')
  return response.json()
}

export default function Budget() {
  const [month, setMonth] = useState(() => new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit',
  }).format(new Date()))
  const [categories, setCategories] = useState<Category[]>([])
  const [amounts, setAmounts] = useState<Record<string, string>>({})
  const [name, setName] = useState('')
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoaded(false)
    Promise.all([api('/v1/categories'), api(`/v1/budgets/${month}`)]).then(([cats, budget]) => {
      if (cancelled) return
      setCategories(cats.categories)
      setAmounts(Object.fromEntries(Object.entries(budget.allowances as Record<string, number>)
        .map(([id, cents]) => [id, (cents / 100).toFixed(2)])))
      setMessage('')
      setLoaded(true)
    }).catch(() => { if (!cancelled) setMessage('Unable to load the budget. Please try again.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [month])

  async function addCategory(event: React.FormEvent) {
    event.preventDefault()
    try {
      const category = await api('/v1/categories', { method: 'POST', body: JSON.stringify({ name }) })
      setCategories(current => [...current, category]); setName(''); setMessage('Category added.')
    } catch { setMessage('Unable to add category. Please try again.') }
  }

  async function save() {
    const allowances: Record<string, number> = {}
    for (const category of categories) {
      const value = (amounts[category.id] || '').trim()
      if (!value) continue
      if (!/^\d{1,7}(\.\d{1,2})?$/.test(value)) { setMessage('Enter nonnegative amounts with at most two decimal places.'); return }
      const [dollars, cents = ''] = value.split('.')
      allowances[category.id] = Number(dollars) * 100 + Number(cents.padEnd(2, '0'))
    }
    try {
      await api(`/v1/budgets/${month}`, { method: 'PUT', body: JSON.stringify({ allowances }) })
      setMessage('Monthly allowances saved.')
    } catch { setMessage('Unable to save the budget. Please try again.') }
  }

  return <section className="overview" aria-label="Monthly budget">
    <div className="section-head"><div><p className="eyebrow">NO ROLLOVER</p><h2>Monthly allowances</h2></div>
      <label>Month <input type="month" value={month} onChange={event => setMonth(event.target.value)} /></label></div>
    <p>Set a separate allowance for each calendar month. Spending is not calculated yet.</p>
    <form onSubmit={event => void addCategory(event)}><label>New category <input value={name} maxLength={80} onChange={event => setName(event.target.value)} required /></label> <button type="submit">Add category</button></form>
    {categories.map(category => <p key={category.id}><label>{category.name} ($) <input inputMode="decimal" value={amounts[category.id] ?? ''} onChange={event => setAmounts({ ...amounts, [category.id]: event.target.value })} /></label></p>)}
    <button disabled={loading || !loaded} onClick={() => void save()}>Save allowances</button>
    <p role="status">{message}</p>
  </section>
}

import { useEffect, useState } from 'react'
import { accessToken, apiUrl } from './auth'

type ForecastData = { as_of: string; starting_cents: number | null; oldest_sync_completed_at: number | null; warnings: string[]; months: { month: string; income_cents: number; expense_cents: number; fulfilled_count: number; pending_fulfilled_count: number; projected_cents: number | null }[] }
const money = (cents: number) => (cents / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD' })

export default function Forecast() {
  const [data, setData] = useState<ForecastData | null>(null)
  const [message, setMessage] = useState('Loading forecast…')
  const [version, setVersion] = useState(0)
  useEffect(() => {
    let cancelled = false
    setMessage('Loading forecast…')
    fetch(`${apiUrl}/v1/forecast`, { headers: { Authorization: `Bearer ${accessToken()}` }, cache: 'no-store' })
      .then(response => { if (!response.ok) throw new Error('request failed'); return response.json() })
      .then(result => { if (!cancelled) { setData(result); setMessage('') } })
      .catch(() => { if (!cancelled) { setData(null); setMessage('Could not load forecast. Try again after account synchronization finishes.') } })
    return () => { cancelled = true }
  }, [version])
  return <section className="overview" aria-label="Twelve-month forecast">
    <h2>12-month forecast</h2>
    <p>Estimate from today through the next 11 calendar months. Earlier unmatched occurrences are not included. Matched occurrences, including pending transactions, are treated as fulfilled.</p>
    <button onClick={() => setVersion(v => v + 1)}>Refresh forecast</button>
    <p role="status">{message}</p>
    {data && <><p>Estimate as of {data.as_of} · Starting balance: {data.starting_cents == null ? 'Unavailable' : money(data.starting_cents)}{data.oldest_sync_completed_at != null && ` · Oldest sync completed ${new Date(data.oldest_sync_completed_at * 1000).toLocaleString()}`}</p>
      <ul>{data.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
      <table><thead><tr><th>Month</th><th>Expected income</th><th>Expected expenses</th><th>Fulfilled</th><th>Estimated balance</th></tr></thead><tbody>
        {data.months.map(month => <tr key={month.month}><td>{month.month}</td><td>{money(month.income_cents)}</td><td>{money(month.expense_cents)}</td><td>{month.fulfilled_count}{month.pending_fulfilled_count > 0 && ` (${month.pending_fulfilled_count} pending)`}</td><td>{month.projected_cents == null ? 'Unavailable' : money(month.projected_cents)}</td></tr>)}
      </tbody></table></>}
  </section>
}

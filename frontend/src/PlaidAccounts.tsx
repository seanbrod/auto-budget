import { useEffect, useState } from 'react'
import { accessToken, apiUrl } from './auth'

type Account = { account_id: string; name: string; mask?: string | null; type: string; balances: { current?: number | null; iso_currency_code?: string | null } }
type Item = { item_id: string; status: string }
type PlaidHandler = { open: () => void; destroy: () => void }
declare global {
  interface Window { Plaid?: { create: (options: { token: string; onSuccess: (token: string) => void; onExit: () => void }) => PlaidHandler } }
}

async function api(path: string, init: RequestInit = {}) {
  const token = accessToken()
  if (!token) throw new Error('Your session has expired. Sign in again.')
  const response = await fetch(`${apiUrl}${path}`, { ...init, cache: 'no-store', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } })
  if (!response.ok) throw new Error('Could not complete the request. Please try again.')
  return response.json()
}

export default function PlaidAccounts() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [items, setItems] = useState<Item[]>([])
  const [message, setMessage] = useState('Sandbox connections use test data only.')
  const [busy, setBusy] = useState(false)

  async function refresh() {
    const [accountResult, itemResult]: [{ accounts: Account[] }, { items: Item[] }] = await Promise.all([
      api('/v1/accounts'), api('/v1/plaid/items'),
    ])
    setAccounts(accountResult.accounts)
    setItems(itemResult.items)
  }

  async function disconnect(itemId: string) {
    if (!window.confirm('Disconnect this Sandbox connection and remove its stored test data?')) return
    setBusy(true)
    try {
      await api(`/v1/plaid/items/${encodeURIComponent(itemId)}`, { method: 'DELETE' })
      await refresh()
      setMessage('Sandbox connection disconnected.')
    } catch { setMessage('Disconnect is incomplete. Refresh and retry; investigate in Plaid if it continues to fail.'); void refresh().catch(() => undefined) }
    finally { setBusy(false) }
  }

  useEffect(() => { void refresh().catch(() => setMessage('Could not load Sandbox accounts.')) }, [])

  async function connect() {
    setBusy(true)
    try {
      if (!window.Plaid) {
        await new Promise<void>((resolve, reject) => {
          const script = document.createElement('script')
          script.src = 'https://cdn.plaid.com/link/v2/stable/link-initialize.js'
          script.onload = () => resolve()
          script.onerror = () => reject(new Error('Could not load Plaid Link.'))
          document.head.appendChild(script)
        })
      }
      const result: { link_token: string } = await api('/v1/plaid/link-token', { method: 'POST' })
      if (!window.Plaid) throw new Error('Could not load Plaid Link.')
      const handler = window.Plaid.create({
        token: result.link_token,
        onSuccess: (publicToken) => {
          void api('/v1/plaid/exchange', { method: 'POST', body: JSON.stringify({ public_token: publicToken }) })
            .then((result: { status: string }) => {
              setMessage(result.status === 'recovery_pending' ? 'Connection saved, but sync could not be queued. Weekly recovery will retry.' : 'Sandbox connection added. Sync may take a few minutes; refresh accounts to check.')
              void refresh().catch(() => undefined)
              handler.destroy()
            })
            .catch(() => { setMessage('Connection status is uncertain. Refresh accounts and investigate before connecting again.'); void refresh().catch(() => undefined); handler.destroy() })
            .finally(() => setBusy(false))
        },
        onExit: () => { handler.destroy(); setBusy(false) },
      })
      handler.open()
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not start Plaid Link.'); setBusy(false) }
  }

  return <div className="empty-state">
    <h3>Sandbox accounts</h3><p>{message}</p>
    <button className="sign-in" disabled={busy} onClick={() => void connect()}>Connect test account</button>{' '}
    <button className="sign-in" onClick={() => void refresh().catch(() => setMessage('Could not refresh Sandbox accounts.'))}>Refresh accounts</button>
    {accounts.length > 0 && <ul>{accounts.map(account => <li key={account.account_id}>{account.name} {account.mask && `••${account.mask}`} — {account.balances.current == null ? 'Balance unavailable' : `${account.balances.iso_currency_code || 'USD'} ${account.balances.current.toFixed(2)}`}</li>)}</ul>}
    {items.length > 0 && <div><p>Sandbox connections</p>{items.map(item => <div key={item.item_id}><span>Connection …{item.item_id.slice(-8)}{item.status !== 'active' && ' — disconnect incomplete'}</span>{' '}<button disabled={busy} onClick={() => void disconnect(item.item_id)}>{item.status === 'deleting' ? 'Retry disconnect' : 'Disconnect'}</button></div>)}</div>}
  </div>
}

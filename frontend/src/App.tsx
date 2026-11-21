import { useEffect, useState } from 'react'
import { accessToken, apiUrl, completeSignIn, configured, signIn, signOut } from './auth'
import PlaidAccounts from './PlaidAccounts'
import Budget from './Budget'
import Transactions from './Transactions'
import Rules from './Rules'
import Expected from './Expected'
import Forecast from './Forecast'

function App() {
  const [status, setStatus] = useState('Checking sign-in…')
  const [signedIn, setSignedIn] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function initialize() {
      if (!configured) { setStatus('Configure the app using the README after deploying Phase 2.'); return }
      try {
        await completeSignIn()
        const token = accessToken()
        if (!token) { if (!cancelled) setStatus('Sign in to access your household.'); return }
        const response = await fetch(`${apiUrl}/api/me`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' })
        if (response.status === 401) { sessionStorage.removeItem('auto-budget-access-token'); throw new Error('Your session has expired. Sign in again.') }
        if (response.status === 403) throw new Error('This account does not have access to a household.')
        if (!response.ok) throw new Error('Could not connect to the API. Please try again.')
        if (!cancelled) { setSignedIn(true); setStatus('Household connected') }
      } catch (error) { if (!cancelled) setStatus(error instanceof Error ? error.message : 'Sign-in failed.') }
    }
    void initialize()
    return () => { cancelled = true }
  }, [])

  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="/" aria-label="Auto Budget home"><span className="brand-mark">a<span>.</span></span><span>auto budget</span></a>{accessToken() && <button className="sign-out" onClick={signOut}>Sign out</button>}</header>
    <main><div className="heading"><div><p className="eyebrow">PLAID SANDBOX</p><h1>Test your<br /><em>connections.</em></h1><p className="intro">Connect test accounts and check their balances.</p></div></div>
      <section className="overview" aria-label="Sandbox connections"><div className="section-head"><div><p className="eyebrow">TEST DATA ONLY</p><h2>Sandbox connections</h2></div><span className="status" role="status">{signedIn && <span className="dot" />}{status}</span></div>
        {signedIn ? <PlaidAccounts /> : <div className="empty-state"><div className="empty-icon" aria-hidden="true">↗</div><h3>Welcome to your budget.</h3><p>Sign in to securely access your household dashboard.</p>{configured && <button className="sign-in" onClick={() => void signIn().catch((error: Error) => setStatus(error.message))}>Sign in</button>}</div>}
      </section>
      {signedIn && <Budget />}
      {signedIn && <Transactions />}
      {signedIn && <Rules />}
      {signedIn && <Expected />}
      {signedIn && <Forecast />}
    </main></div>
}

export default App

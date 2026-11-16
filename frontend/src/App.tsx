import { useQuery } from '@tanstack/react-query'

async function checkApi(): Promise<boolean> {
  const response = await fetch('/api/health')
  if (!response.ok) throw new Error('API unavailable')
  const body: unknown = await response.json()
  if (typeof body !== 'object' || body === null || !('status' in body) || body.status !== 'ok') throw new Error('Invalid response')
  return true
}

function App() {
  const { isPending, isError } = useQuery({ queryKey: ['health'], queryFn: checkApi, retry: false })
  const year = new Date().getFullYear()

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Auto Budget home"><span className="brand-mark">a<span>.</span></span><span>auto budget</span></a>
        <span className="topbar-note">A little clarity for your money</span>
      </header>
      <main>
        <div className="heading"><div><p className="eyebrow">YOUR FINANCIAL OVERVIEW</p><h1>Make room for<br /><em>what matters.</em></h1><p className="intro">A clearer picture of your money, one month at a time.</p></div><div className="year-badge"><span>BUDGET YEAR</span><strong>{year}</strong></div></div>
        <section className="overview" aria-label="Budget overview">
          <div className="section-head"><div><p className="eyebrow">THE BIG PICTURE</p><h2>Your year at a glance</h2></div><span className="status"><span className={isPending ? 'dot waiting' : isError ? 'dot offline' : 'dot'} />{isPending ? 'Checking API' : isError ? 'API offline' : 'API connected'}</span></div>
          <div className="empty-state"><div className="empty-icon" aria-hidden="true">↗</div><h3>Your dashboard starts here.</h3><p>Account connections, budgets, and spending insights are coming in the next phases. For now, this is your local foundation.</p><span className="empty-label">NO FINANCIAL DATA CONNECTED</span></div>
        </section>
        <div className="footnote"><span>DESIGNED FOR THE LONG VIEW</span><span>Built one step at a time <span aria-hidden="true">✳</span></span></div>
      </main>
    </div>
  )
}

export default App

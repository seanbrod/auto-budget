const domain = import.meta.env.VITE_COGNITO_DOMAIN as string | undefined
const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID as string | undefined
export const apiUrl = import.meta.env.VITE_API_URL as string | undefined
const redirectUri = 'http://localhost:5173/'
const tokenKey = 'auto-budget-access-token'
const pendingKey = 'auto-budget-oauth-pending'

export const configured = Boolean(domain && clientId && apiUrl)

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export async function signIn(): Promise<void> {
  if (!domain || !clientId) throw new Error('Sign-in is not configured.')
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)))
  const state = base64url(crypto.getRandomValues(new Uint8Array(32)))
  const challenge = base64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))))
  sessionStorage.setItem(pendingKey, JSON.stringify({ verifier, state }))
  const params = new URLSearchParams({ response_type: 'code', client_id: clientId, redirect_uri: redirectUri, scope: 'openid', state, code_challenge: challenge, code_challenge_method: 'S256' })
  window.location.assign(`${domain}/oauth2/authorize?${params}`)
}

let callbackPromise: Promise<void> | undefined
export function completeSignIn(): Promise<void> {
  if (callbackPromise) return callbackPromise
  const params = new URLSearchParams(window.location.search)
  const code = params.get('code')
  const state = params.get('state')
  if (!code && !params.has('error')) return Promise.resolve()
  history.replaceState(null, '', window.location.pathname)
  callbackPromise = (async () => {
    const pending = sessionStorage.getItem(pendingKey)
    sessionStorage.removeItem(pendingKey)
    if (!code || !state || !pending || !domain || !clientId) throw new Error('Sign-in was not completed. Please try again.')
    const parsed: { state: string; verifier: string } = JSON.parse(pending)
    if (state !== parsed.state) throw new Error('Sign-in could not be verified. Please try again.')
    const response = await fetch(`${domain}/oauth2/token`, {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'authorization_code', client_id: clientId, code, redirect_uri: redirectUri, code_verifier: parsed.verifier }),
    })
    if (!response.ok) throw new Error('Sign-in could not be completed. Please try again.')
    const result: { access_token?: string; expires_in?: number } = await response.json()
    if (!result.access_token || !result.expires_in) throw new Error('Invalid sign-in response.')
    sessionStorage.setItem(tokenKey, JSON.stringify({ token: result.access_token, expires: Date.now() + result.expires_in * 1000 }))
  })()
  return callbackPromise
}

export function accessToken(): string | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(tokenKey) || 'null') as { token: string; expires: number } | null
    if (value && value.expires > Date.now() + 5000) return value.token
  } catch { /* Discard invalid session data. */ }
  sessionStorage.removeItem(tokenKey)
  return null
}

export function signOut(): void {
  sessionStorage.removeItem(tokenKey)
  sessionStorage.removeItem(pendingKey)
  if (domain && clientId) {
    const params = new URLSearchParams({ client_id: clientId, logout_uri: redirectUri })
    window.location.assign(`${domain}/logout?${params}`)
  }
}

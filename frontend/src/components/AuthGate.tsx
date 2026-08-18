import React, { createContext, FormEvent, useContext, useEffect, useRef, useState } from 'react'
import { LockKeyhole, Radar, ShieldCheck } from 'lucide-react'
import { getAuthStatus, login, logout as endSession } from '../lib/api'

type GateState = 'checking' | 'authenticated' | 'locked' | 'unavailable'
const AuthContext = createContext({ logout: async () => {} })

export const useAuth = () => useContext(AuthContext)

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GateState>('checking')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const passwordRef = useRef<HTMLInputElement>(null)

  const check = async () => {
    try {
      const status = await getAuthStatus()
      setState(status.authenticated ? 'authenticated' : 'locked')
    } catch {
      setState('unavailable')
    }
  }

  useEffect(() => { void check() }, [])
  useEffect(() => {
    const lock = () => {
      setPassword('')
      setError(null)
      setState('locked')
    }
    window.addEventListener('market-monitor:auth-required', lock)
    return () => window.removeEventListener('market-monitor:auth-required', lock)
  }, [])
  useEffect(() => {
    if (state === 'locked') passwordRef.current?.focus()
  }, [state])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const status = await login(password)
      if (!status.authenticated) throw new Error('Authentication failed')
      setPassword('')
      setState('authenticated')
    } catch (requestError: any) {
      const status = requestError?.response?.status
      setError(status === 401 ? 'That password is not valid.' : 'Sign-in is temporarily unavailable.')
      passwordRef.current?.focus()
    } finally {
      setSubmitting(false)
    }
  }

  const logout = async () => {
    try { await endSession() } finally {
      setPassword('')
      setError(null)
      setState('locked')
    }
  }

  if (state === 'authenticated') {
    return <AuthContext.Provider value={{ logout }}>{children}</AuthContext.Provider>
  }

  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand" aria-hidden="true"><Radar size={23} /></div>
        <span className="auth-eyebrow"><ShieldCheck size={13} /> Private market workspace</span>
        <h1 id="auth-title">Market Monitor</h1>
        {state === 'checking' ? (
          <div className="auth-checking" role="status"><span className="ui-spinner" /> Verifying your session…</div>
        ) : state === 'unavailable' ? (
          <div className="auth-message" role="alert">
            <strong>Workspace unavailable</strong>
            <span>The secure access check could not be completed.</span>
            <button type="button" className="ui-button ui-button--secondary" onClick={() => { setState('checking'); void check() }}>Try again</button>
          </div>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            <p>Sign in to view market evidence or run operational actions.</p>
            <label className="field">
              <span className="field-label">Workspace password</span>
              <span className="auth-password-field"><LockKeyhole size={17} aria-hidden="true" /><input ref={passwordRef} className="ui-input" type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required /></span>
            </label>
            {error && <div className="auth-error" role="alert">{error}</div>}
            <button className="ui-button ui-button--primary auth-submit" type="submit" disabled={submitting || password.length === 0}>
              {submitting && <span className="ui-spinner" />} {submitting ? 'Signing in…' : 'Open workspace'}
            </button>
          </form>
        )}
      </section>
      <p className="auth-footnote">Session access is stored in a secure, HttpOnly cookie.</p>
    </main>
  )
}

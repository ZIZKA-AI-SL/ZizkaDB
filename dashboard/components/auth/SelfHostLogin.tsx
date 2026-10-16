'use client'

import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { API, getSelfHostConfig, selfHostLogin } from '@/lib/api'
import type { SelfHostLoginMode } from '@/lib/api'
import { setToken } from '@/lib/auth'
import { BrandLogo } from '@/components/BrandLogo'

type Status = 'loading' | 'unreachable' | 'not_self_hosted' | 'ready'

const AUTO_SIGNIN_KEY = 'zizkadb-auto-signin-at'
const AUTO_SIGNIN_COOLDOWN_MS = 10_000

/**
 * Self-hosted sign-in. No email, signup or marketing: the API decides between
 * a one-click button (ENV=development) and an admin token (ENV=production).
 * With `auto`, one-click mode signs in without the click — the button guards
 * nothing there, so a fresh local install lands straight on the dashboard.
 */
export function SelfHostLogin({ next, auto = false }: { next: string; auto?: boolean }) {
  const [status, setStatus] = useState<Status>('loading')
  const [mode, setMode] = useState<SelfHostLoginMode | null>(null)
  const [token, setTokenInput] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [navigating, setNavigating] = useState(false)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  const retry = useCallback(() => setAttempt((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    setStatus('loading')
    getSelfHostConfig()
      .then((cfg) => {
        if (cancelled) return
        // Self-hosted dashboard pointed at an API that isn't (DEPLOYMENT_MODE
        // mismatch) — no sign-in can work, so explain the config fix instead.
        if (!cfg.self_hosted) {
          setStatus('not_self_hosted')
          return
        }
        setMode(cfg.login)
        setStatus('ready')
      })
      .catch(() => {
        if (!cancelled) setStatus('unreachable')
      })
    return () => {
      cancelled = true
    }
  }, [attempt])

  const signIn = useCallback(
    async (adminToken?: string) => {
      setSubmitting(true)
      setError('')
      try {
        const data = await selfHostLogin(adminToken)
        setToken(data.access_token)
        setNavigating(true)
        window.location.assign(next)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Sign-in failed')
      } finally {
        setSubmitting(false)
      }
    },
    [next],
  )

  useEffect(() => {
    if (!auto || status !== 'ready' || mode !== 'one_click') return
    // At most one automatic attempt per tab every few seconds: if the session
    // doesn't stick, the dashboard sends us back here and the button shows
    // instead of a redirect loop.
    const last = Number(window.sessionStorage.getItem(AUTO_SIGNIN_KEY) || 0)
    if (Date.now() - last < AUTO_SIGNIN_COOLDOWN_MS) return
    window.sessionStorage.setItem(AUTO_SIGNIN_KEY, String(Date.now()))
    signIn()
  }, [auto, status, mode, signIn])

  if (navigating) {
    return (
      <Shell>
        <p style={{ fontSize: 15, color: '#555', textAlign: 'center' }}>Signing you in…</p>
      </Shell>
    )
  }

  return (
    <Shell>
      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
          <span style={badge}>SELF-HOSTED</span>
          <span style={{ fontSize: 13, fontWeight: 600, color: '#15803d' }}>
            Your ZizkaDB instance
          </span>
        </div>

        {status === 'loading' && (
          <p style={muted}>Connecting to the API…</p>
        )}

        {status === 'unreachable' && (
          <>
            <p style={{ ...muted, color: '#b91c1c' }}>
              Can&apos;t reach the ZizkaDB API at {API || 'this host (/v1)'}. Is the stack running?
            </p>
            <button onClick={retry} style={primaryBtn}>
              Retry
            </button>
          </>
        )}

        {status === 'not_self_hosted' && (
          <p style={muted}>
            The API at {API || 'this host (/v1)'} is not running in self-hosted mode. Set{' '}
            <code>DEPLOYMENT_MODE=self_hosted</code> in your .env and restart the API.
          </p>
        )}

        {status === 'ready' && mode === 'one_click' && (
          <>
            <p style={muted}>Open the dashboard directly. No account needed.</p>
            <button onClick={() => signIn()} disabled={submitting} style={{ ...primaryBtn, opacity: submitting ? 0.6 : 1 }}>
              {submitting ? 'Connecting...' : 'Open my dashboard →'}
            </button>
          </>
        )}

        {status === 'ready' && mode === 'admin_token' && (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (token.trim()) signIn(token.trim())
            }}
          >
            <p style={muted}>Enter the admin token from your server&apos;s .env (SELFHOST_ADMIN_TOKEN).</p>
            <input
              type='password'
              autoComplete='current-password'
              autoFocus
              aria-label='Admin token'
              placeholder='Admin token'
              value={token}
              onChange={(e) => setTokenInput(e.target.value)}
              style={input}
            />
            <button type='submit' disabled={submitting || !token.trim()} style={{ ...primaryBtn, opacity: submitting || !token.trim() ? 0.6 : 1 }}>
              {submitting ? 'Signing in...' : 'Open my dashboard →'}
            </button>
          </form>
        )}

        {status === 'ready' && mode === 'unavailable' && (
          <p style={muted}>
            Dashboard login is turned off on this server. Set <code>SELFHOST_ADMIN_TOKEN</code> in your
            .env and restart the API, then reload this page.
          </p>
        )}

        {error && <p style={{ fontSize: 13, color: '#ef4444', margin: '12px 0 0' }}>{error}</p>}
      </div>
    </Shell>
  )
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#fafafa',
        fontFamily: 'Inter, system-ui, sans-serif',
      }}
    >
      <div style={{ width: '100%', maxWidth: 400, padding: '0 24px' }}>
        <div style={{ textAlign: 'center', marginBottom: 36 }}>
          <BrandLogo variant='full' suffix='The operational database for AI agents' />
        </div>
        {children}
      </div>
    </div>
  )
}

const card: CSSProperties = {
  background: '#f0fdf4',
  border: '1px solid #bbf7d0',
  borderRadius: 12,
  padding: '16px 20px',
}

const badge: CSSProperties = {
  background: '#22c55e',
  color: '#fff',
  fontSize: 11,
  fontWeight: 700,
  padding: '2px 7px',
  borderRadius: 99,
  letterSpacing: '0.05em',
}

const muted: CSSProperties = { fontSize: 13, color: '#166534', margin: '0 0 12px', lineHeight: 1.5 }

const input: CSSProperties = {
  width: '100%',
  boxSizing: 'border-box',
  padding: '10px 12px',
  borderRadius: 9,
  border: '1px solid #bbf7d0',
  fontSize: 14,
  marginBottom: 10,
  background: '#fff',
}

const primaryBtn: CSSProperties = {
  width: '100%',
  padding: '10px',
  borderRadius: 9,
  fontSize: 14,
  fontWeight: 600,
  background: '#16a34a',
  color: '#fff',
  border: 'none',
  cursor: 'pointer',
}

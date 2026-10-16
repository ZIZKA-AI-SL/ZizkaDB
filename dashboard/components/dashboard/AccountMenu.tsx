'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ChevronDown, LogOut, Settings } from 'lucide-react'
import { API, getBillingStatus, type BillingStatus } from '@/lib/api'
import { clearToken, getSessionEmail, getToken } from '@/lib/auth'
import { IS_DEV_MODE, IS_SELF_HOSTED } from '@/lib/constants'
import { colors, radii } from '@/lib/design-tokens'
import { useConnectionHealth, type HealthState } from '@/hooks/useConnectionHealth'
import { useEdition } from '@/hooks/useEdition'

const PLAN_LABELS: Record<string, string> = { pro: 'Pro', team: 'Team' }

const HEALTH_META: Record<HealthState, { color: string; label: string }> = {
  ok: { color: colors.success, label: 'API connected' },
  error: { color: colors.danger, label: 'API unreachable' },
  checking: { color: colors.textFaint, label: 'Checking…' },
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  } catch {
    return iso
  }
}

/**
 * Everything about "who am I and is the backend up" — plan, trial, email,
 * connection health, API host, tenant, plus Settings and Sign out — collapsed
 * into one compact header control and a popover.
 *
 * Replaces the two full-width banners (TenantPlanBanner + ConnectionStatus)
 * that used to sit at the top of the content area, so the main workspace is
 * dedicated to events and debugging.
 */
export function AccountMenu() {
  const router = useRouter()
  const [open, setOpen] = useState(false)
  const [billing, setBilling] = useState<BillingStatus | null>(null)
  // The email lives in the JWT in localStorage, which doesn't exist during SSR.
  // Reading it at render time makes the server ('U') and client (real initial)
  // disagree and React throws a hydration mismatch, so resolve it after mount.
  const [email, setEmail] = useState<string | null>(null)
  const health = useConnectionHealth()
  const isOss = useEdition().edition === 'oss'
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setEmail(getSessionEmail())
    const token = getToken()
    if (!token) return
    let cancelled = false
    getBillingStatus(token)
      .then((s) => {
        if (!cancelled) setBilling(s)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const planLabel = billing?.plan ? (PLAN_LABELS[billing.plan] ?? billing.plan) : null
  const trialing = billing?.subscription_status === 'trialing'
  const hm = HEALTH_META[health]
  const initial = (email?.[0] ?? 'U').toUpperCase()
  // Plan/trial is a managed-cloud billing concept — never meaningful on a
  // self-hosted install, so hide it there. The email in dev/self-host mode is
  // the synthetic `dev@localhost` owner placeholder, so hide that too.
  const showBilling = !isOss && !!planLabel
  const showEmail = !!email && !IS_DEV_MODE && !IS_SELF_HOSTED

  function signOut() {
    clearToken()
    // Self-hosted one-click login signs in automatically; say this was a sign-out.
    router.push(IS_SELF_HOSTED ? '/login?signed_out=1' : '/login')
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account and connection"
        className="btn-hover flex items-center gap-2 pl-1.5 pr-2 py-1.5"
        style={{
          background: open ? colors.surfaceHover : 'transparent',
          border: `1px solid ${open ? colors.borderStrong : colors.border}`,
          borderRadius: radii.md,
        }}
      >
        <span
          className="relative flex items-center justify-center text-xs font-semibold shrink-0"
          style={{
            width: 24,
            height: 24,
            borderRadius: radii.full,
            background: colors.surfaceHover,
            color: colors.text,
          }}
        >
          {initial}
          {/* Health dot overlay — status is visible without opening the menu. */}
          <span
            aria-hidden="true"
            className="absolute"
            style={{
              right: -1,
              bottom: -1,
              width: 8,
              height: 8,
              borderRadius: radii.full,
              background: hm.color,
              border: `1.5px solid ${colors.surfaceAlt}`,
            }}
          />
        </span>
        {showBilling && (
          <span className="text-xs font-medium hidden sm:inline" style={{ color: colors.textMuted }}>
            {planLabel}
          </span>
        )}
        <ChevronDown size={13} style={{ color: colors.textFaint }} />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 mt-2 z-50"
          style={{
            width: 280,
            background: colors.surface,
            border: `1px solid ${colors.borderStrong}`,
            borderRadius: radii.lg,
            boxShadow: '0 12px 32px rgba(0,0,0,0.5)',
          }}
        >
          {/* Identity — plan/trial (managed only) + email (real accounts only) */}
          {(showBilling || showEmail) && (
            <div className="px-4 py-3" style={{ borderBottom: `1px solid ${colors.border}` }}>
              {showBilling && (
                <div className="flex items-center gap-2 flex-wrap">
                  <span
                    className="text-xs font-semibold px-2 py-0.5"
                    style={{
                      background: colors.surfaceHover,
                      color: colors.text,
                      borderRadius: radii.full,
                    }}
                  >
                    {planLabel}
                  </span>
                  {trialing && billing?.trial_ends_at && (
                    <span className="text-xs" style={{ color: colors.textMuted }}>
                      Trial ends {formatDate(billing.trial_ends_at)}
                    </span>
                  )}
                  {billing?.subscription_status === 'active' && !trialing && (
                    <span className="text-xs" style={{ color: colors.success }}>
                      Active
                    </span>
                  )}
                </div>
              )}
              {showEmail && (
                <p className="text-sm mt-1.5 truncate" style={{ color: colors.text }} title={email ?? undefined}>
                  {email}
                </p>
              )}
            </div>
          )}

          {/* Connection */}
          <div className="px-4 py-3" style={{ borderBottom: `1px solid ${colors.border}` }}>
            <div className="flex items-center gap-2">
              <span
                aria-hidden="true"
                style={{ width: 7, height: 7, borderRadius: radii.full, background: hm.color }}
              />
              <span className="text-xs" style={{ color: colors.text }}>
                {hm.label}
              </span>
            </div>
            <code
              className="block text-xs mt-1.5 truncate"
              style={{ color: colors.textFaint, fontFamily: 'monospace' }}
              title={API || 'same-origin'}
            >
              {API || 'same-origin (nginx)'}
            </code>
            {(IS_DEV_MODE || IS_SELF_HOSTED) && (
              <p className="text-xs mt-1" style={{ color: colors.textFaint }}>
                Self-hosted · owner tenant
              </p>
            )}
          </div>

          {/* Actions */}
          <div className="p-1.5">
            <Link
              href="/dashboard/settings"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="opt-hover flex items-center gap-2.5 px-2.5 py-2 text-sm"
              style={{ color: colors.text, borderRadius: radii.md }}
            >
              <Settings size={15} style={{ color: colors.textMuted }} />
              Settings
            </Link>
            <button
              onClick={signOut}
              role="menuitem"
              className="opt-hover w-full flex items-center gap-2.5 px-2.5 py-2 text-sm"
              style={{ color: colors.text, borderRadius: radii.md }}
            >
              <LogOut size={15} style={{ color: colors.textMuted }} />
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

'use client'

import { useState } from 'react'
import { KeyRound, Plus } from 'lucide-react'
import { Button, CopyButton } from '@/components/ui'
import { API, createApiKey } from '@/lib/api'
import { getToken } from '@/lib/auth'
import { colors, radii } from '@/lib/design-tokens'

/**
 * First-run / OSS key creation. Creates an unassigned named key (binds to the
 * first agent that uses it), reveals it once, and shows how to wire it into an
 * app. This is the entry point that breaks the chicken-and-egg where a key used
 * to require a pre-existing agent.
 */
export function CreateApiKeyCard({ onCreated }: { onCreated?: () => void }) {
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [newKey, setNewKey] = useState<string | null>(null)

  // Builds with an empty API URL serve the API on the dashboard's own origin
  // (single-container image), so the SDK should point at this page's origin.
  const host = API || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000')

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    const token = getToken()
    if (!token) {
      setError('Please sign in again.')
      return
    }
    setCreating(true)
    setError(null)
    try {
      const res = await createApiKey(token, name.trim() || 'default')
      setNewKey(res.key)
      setName('')
      onCreated?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create API key')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div
      className="p-5"
      style={{ background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: radii.lg }}
    >
      <div className="flex items-center gap-2 mb-1">
        <KeyRound size={15} style={{ color: colors.textMuted }} />
        <h2 className="text-sm font-medium" style={{ color: colors.textStrong }}>
          Create your API key
        </h2>
      </div>
      <p className="text-xs mb-4" style={{ color: colors.textMuted }}>
        Name a key, drop it into your app, and start logging. The key is
        unassigned until the first agent uses it, then it stays bound to that
        agent only.
      </p>

      {newKey ? (
        <KeyReveal apiKey={newKey} host={host} onDone={() => setNewKey(null)} />
      ) : (
        <form className="flex flex-col sm:flex-row gap-2" onSubmit={handleCreate}>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Key name (e.g. my-app production)"
            disabled={creating}
            aria-label="API key name"
            className="flex-1 px-3 py-2 text-sm outline-none disabled:opacity-40"
            style={{
              background: colors.bg,
              border: `1px solid ${colors.border}`,
              borderRadius: radii.md,
              color: colors.text,
            }}
          />
          <Button type="submit" tone="primary" disabled={creating}>
            <Plus size={14} />
            {creating ? 'Creating…' : 'Create key'}
          </Button>
        </form>
      )}

      {error && (
        <p className="text-xs mt-2" style={{ color: colors.danger }}>
          {error}
        </p>
      )}
    </div>
  )
}

function KeyReveal({ apiKey, host, onDone }: { apiKey: string; host: string; onDone: () => void }) {
  const snippet = `ZIZKADB_HOST=${host}\nZIZKADB_API_KEY=${apiKey}`
  return (
    <div className="space-y-3">
      <div
        className="p-3"
        style={{ background: colors.successBg, border: `1px solid ${colors.success}40`, borderRadius: radii.md }}
      >
        <div className="flex items-center justify-between mb-2">
          <p className="text-xs font-medium" style={{ color: colors.success }}>
            Your API key — copy it now, it won&apos;t be shown again
          </p>
          <CopyButton value={apiKey} label="Copy key" />
        </div>
        <code
          className="block text-xs font-mono px-2 py-1.5 overflow-x-auto"
          style={{ background: colors.bg, color: colors.text, borderRadius: radii.sm }}
        >
          {apiKey}
        </code>
      </div>

      <div>
        <div className="flex items-center justify-between mb-1.5">
          <p className="text-xs font-medium uppercase tracking-wide" style={{ color: colors.textFaint }}>
            Use it in your app
          </p>
          <CopyButton value={snippet} label="Copy" />
        </div>
        <pre
          className="text-xs font-mono p-3 overflow-x-auto"
          style={{ background: colors.bg, color: colors.text, borderRadius: radii.md }}
        >
          {snippet}
        </pre>
        <p className="text-xs mt-1.5" style={{ color: colors.textMuted }}>
          Point your SDK at this host (self-hosted keys only work against your own
          instance, not the cloud). Log one event and your agent appears here.
        </p>
      </div>

      <button
        type="button"
        onClick={onDone}
        className="text-xs"
        style={{ color: colors.textMuted }}
      >
        Done
      </button>
    </div>
  )
}

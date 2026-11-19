'use client'

import { Suspense, useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { KeyRound, Plus, X } from 'lucide-react'
import { createAgent, deleteAgent } from '@/lib/api'
import { getToken } from '@/lib/auth'
import { colors, radii } from '@/lib/design-tokens'
import { Button, CopyButton, EmptyState, ErrorState, PageHeader, Skeleton } from '@/components/ui'
import { FleetTable } from '@/components/dashboard/FleetTable'
import { useAgents } from '@/hooks/useAgents'
import { useEdition } from '@/hooks/useEdition'

function OneTimeKeyBanner({ apiKey, onDismiss }: { apiKey: string; onDismiss: () => void }) {
  return (
    <div
      className="mb-5 p-4"
      style={{
        background: colors.successBg,
        border: `1px solid ${colors.success}40`,
        borderRadius: radii.lg,
      }}
      role="status"
    >
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <KeyRound size={14} style={{ color: colors.success }} />
          <span className="text-sm font-medium" style={{ color: colors.success }}>
            Save this API key now
          </span>
        </div>
        <button onClick={onDismiss} aria-label="Dismiss" style={{ color: colors.textMuted }}>
          <X size={14} />
        </button>
      </div>
      <p className="text-sm mb-3" style={{ color: colors.textMuted }}>
        This is the only time it will be shown. Store it somewhere safe before dismissing.
      </p>
      <div className="flex items-center gap-2 flex-wrap">
        <code
          className="text-xs font-mono px-3 py-2 break-all flex-1"
          style={{ background: colors.bg, borderRadius: radii.md, color: colors.text }}
        >
          {apiKey}
        </code>
        <CopyButton value={apiKey} />
      </div>
    </div>
  )
}

function CreateAgentForm({
  onCreated,
}: {
  /** Receives the one-time API key (if the server issued one) and refreshes the list. */
  onCreated: (key: string | null) => Promise<void> | void
}) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const agentId = name.trim()
    if (!agentId) return
    const token = getToken()
    if (!token) return

    setBusy(true)
    setError(null)
    try {
      const res = await createAgent(token, agentId)
      setName('')
      setOpen(false)
      await onCreated(res.api_key?.key ?? null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create agent')
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <Button onClick={() => setOpen(true)}>
        <Plus size={14} />
        New agent
      </Button>
    )
  }

  return (
    <form onSubmit={submit} className="flex items-center gap-2 flex-wrap">
      <label className="sr-only" htmlFor="new-agent-name">
        Agent name
      </label>
      <input
        id="new-agent-name"
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="support-bot"
        className="px-3 py-2 text-sm outline-none font-mono"
        style={{
          background: colors.surface,
          border: `1px solid ${colors.borderStrong}`,
          borderRadius: radii.md,
          color: colors.text,
        }}
      />
      <Button type="submit" tone="primary" disabled={busy || !name.trim()}>
        {busy ? 'Creating…' : 'Create'}
      </Button>
      <Button onClick={() => setOpen(false)}>Cancel</Button>
      {error && (
        <span className="text-xs w-full" style={{ color: colors.danger }}>
          {error}
        </span>
      )}
    </form>
  )
}

function FleetContent() {
  const router = useRouter()
  const { edition, loading: editionLoading } = useEdition()
  const { agents, loading, error, refresh } = useAgents()
  const [newKey, setNewKey] = useState<string | null>(null)
  const [deletingAgent, setDeletingAgent] = useState<string | null>(null)

  // Fleet is a managed-plan surface; OSS has no multi-agent view.
  useEffect(() => {
    if (!editionLoading && edition === 'oss') {
      router.replace('/dashboard/activity')
    }
  }, [edition, editionLoading, router])

  const handleCreated = useCallback(
    async (key: string | null) => {
      setNewKey(key)
      await refresh()
    },
    [refresh],
  )

  const handleDelete = useCallback(
    async (agent: string) => {
      if (
        !window.confirm(
          `Delete "${agent}"? This permanently removes the agent and every event it recorded.`,
        )
      ) {
        return
      }
      const token = getToken()
      if (!token) return

      setDeletingAgent(agent)
      try {
        await deleteAgent(token, agent)
        await refresh()
      } finally {
        setDeletingAgent(null)
      }
    },
    [refresh],
  )

  if (editionLoading) return <Skeleton rows={5} />
  if (edition === 'oss') return null
  if (loading) return <Skeleton rows={5} />

  return (
    <>
      <PageHeader
        title="Agent Fleets"
        description="Every agent in this workspace, and how each one is doing."
        actions={<CreateAgentForm onCreated={handleCreated} />}
      />

      {newKey && <OneTimeKeyBanner apiKey={newKey} onDismiss={() => setNewKey(null)} />}

      {error ? (
        <ErrorState message={error} />
      ) : agents.length === 0 ? (
        <EmptyState
          title="No agents yet"
          description="Create an agent to get an API key, or just start logging events (REST API, SDK, MCP or a framework integration) — agents are created on first use."
        />
      ) : (
        <FleetTable agents={agents} onDelete={handleDelete} deletingAgent={deletingAgent} />
      )}
    </>
  )
}

export default function FleetPage() {
  return (
    <Suspense fallback={<Skeleton rows={5} />}>
      <FleetContent />
    </Suspense>
  )
}

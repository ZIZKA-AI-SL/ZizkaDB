'use client'

import { useCallback, useEffect, useState } from 'react'
import { format } from 'date-fns'
import { GitBranch, Layers } from 'lucide-react'
import {
  getSessionTimeline,
  getSessionWhyChain,
  getTenantSessions,
  type AgentEvent,
  type TenantSession,
  type WhyChain,
} from '@/lib/api'
import { getToken } from '@/lib/auth'
import { colors, radii } from '@/lib/design-tokens'
import { EmptyState, ErrorState, Skeleton } from '@/components/ui'
import { eventLabel } from '@/lib/events'
import { EventDot } from './EventList'

export function SessionTimelineSegment() {
  const [sessions, setSessions] = useState<TenantSession[]>([])
  const [selected, setSelected] = useState<TenantSession | null>(null)
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [whyChain, setWhyChain] = useState<WhyChain | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const token = getToken()
    if (!token) return
    setLoading(true)
    getTenantSessions(token)
      .then(setSessions)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load sessions'))
      .finally(() => setLoading(false))
  }, [])

  const openSession = useCallback(async (session: TenantSession) => {
    const token = getToken()
    if (!token) return
    setSelected(session)
    setWhyChain(null)
    setDetailLoading(true)
    try {
      const timeline = await getSessionTimeline(token, session.session_id)
      setEvents(timeline.events)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load session timeline')
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const traceWhy = useCallback(
    async (eventId: string) => {
      const token = getToken()
      if (!token || !selected) return
      setDetailLoading(true)
      try {
        const chain = await getSessionWhyChain(token, selected.session_id, eventId)
        setWhyChain(chain)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to trace causal chain')
      } finally {
        setDetailLoading(false)
      }
    },
    [selected],
  )

  if (loading) return <Skeleton rows={5} />
  if (error) return <ErrorState message={error} />

  if (sessions.length === 0) {
    return (
      <EmptyState
        icon={<Layers size={22} style={{ color: colors.textFaint }} />}
        title="No cross-agent sessions yet"
        description="Log events with the same session_id across agents to see a unified timeline here."
      />
    )
  }

  return (
    <div className="flex flex-col lg:flex-row gap-4">
      <div className="w-full lg:w-80 shrink-0 space-y-2">
        {sessions.map((s) => (
          <button
            key={s.session_id}
            type="button"
            onClick={() => openSession(s)}
            className="w-full text-left p-3"
            style={{
              background: selected?.session_id === s.session_id ? colors.successBg : colors.surface,
              border: `1px solid ${colors.border}`,
              borderRadius: radii.lg,
            }}
          >
            <div className="text-xs font-mono truncate">{s.session_id}</div>
            <div className="text-xs mt-1" style={{ color: colors.textMuted }}>
              {s.event_count} events · {s.agent_count} agent{s.agent_count !== 1 ? 's' : ''}
            </div>
            <div className="text-xs mt-1" style={{ color: colors.textFaint }}>
              {s.agents.join(', ')}
            </div>
          </button>
        ))}
      </div>
      <div className="flex-1 min-w-0">
        {!selected ? (
          <EmptyState
            icon={<GitBranch size={22} style={{ color: colors.textFaint }} />}
            title="Select a session"
            description="View all agents in one timeline — the primary incident view for multi-step runs."
          />
        ) : detailLoading && events.length === 0 ? (
          <Skeleton rows={6} />
        ) : (
          <div className="space-y-2">
            {events.map((e) => (
              <button
                key={e.event_id}
                type="button"
                onClick={() => traceWhy(e.event_id)}
                className="w-full text-left p-3 flex items-start gap-3 row-hover"
                style={{
                  border: `1px solid ${colors.border}`,
                  borderRadius: radii.md,
                  background: colors.surface,
                }}
              >
                <EventDot type={e.event} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-xs">
                    <span className="font-mono font-semibold">{eventLabel(e)}</span>
                    <span style={{ color: colors.textFaint }}>{e.agent}</span>
                  </div>
                  <div className="text-xs" style={{ color: colors.textMuted }}>
                    {format(new Date(e.timestamp), 'HH:mm:ss.SSS')}
                  </div>
                </div>
              </button>
            ))}
            {whyChain && (
              <div
                className="mt-4 p-3 text-xs"
                style={{ border: `1px solid ${colors.border}`, borderRadius: radii.lg }}
              >
                {(whyChain.orphan || whyChain.depth_truncated) && (
                  <p style={{ color: colors.warning }} className="mb-2">
                    Incomplete chain — check logging or use a tenant-wide key.
                  </p>
                )}
                <p className="mb-2 font-medium">{whyChain.chain_length} events in chain</p>
                {whyChain.chain.map((ev) => (
                  <div key={ev.event_id} className="font-mono truncate">
                    {ev.agent} · {eventLabel(ev)}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

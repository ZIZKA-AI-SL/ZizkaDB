'use client'

import { Suspense, useState } from 'react'
import { formatDistanceToNow } from 'date-fns'
import { ScatterChart } from 'lucide-react'
import { GettingStartedChecklist } from '@/components/ConnectionStatus'
import { CreateApiKeyCard } from '@/components/dashboard/CreateApiKeyCard'
import { EmptyState, ErrorState, PageHeader, Skeleton } from '@/components/ui'
import { EventsSegment } from '@/components/dashboard/EventsSegment'
import { SessionsSegment } from '@/components/dashboard/SessionsSegment'
import { SessionTimelineSegment } from '@/components/dashboard/SessionTimelineSegment'
import { TimeTravelSegment } from '@/components/dashboard/TimeTravelSegment'
import { EventTimelinePanel } from '@/components/dashboard/timeline/EventTimelinePanel'
import { useAgents } from '@/hooks/useAgents'
import { useEdition } from '@/hooks/useEdition'
import { useSelectedAgent } from '@/hooks/useSelectedAgent'
import { useAgentEvents } from '@/hooks/useAgentEvents'
import { useAgentStats } from '@/hooks/useAgentStats'
import { colors, radii } from '@/lib/design-tokens'

type Segment = 'events' | 'sessions' | 'timeline' | 'timetravel'

const SEGMENTS: { id: Segment; label: string }[] = [
  { id: 'events', label: 'Events' },
  { id: 'timeline', label: 'Session timeline' },
  { id: 'sessions', label: 'Sessions' },
  { id: 'timetravel', label: 'Time Travel' },
]

function SegmentedControl({
  value,
  onChange,
}: {
  value: Segment
  onChange: (s: Segment) => void
}) {
  return (
    <div
      role="tablist"
      aria-label="Activity view"
      className="inline-flex p-0.5 overflow-x-auto max-w-full shrink-0"
      style={{
        background: colors.surface,
        border: `1px solid ${colors.border}`,
        borderRadius: radii.md,
      }}
    >
      {SEGMENTS.map((s) => {
        const active = s.id === value
        return (
          <button
            key={s.id}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(s.id)}
            className="text-xs font-medium px-3 py-1.5 whitespace-nowrap transition"
            style={{
              background: active ? colors.surfaceHover : 'transparent',
              color: active ? colors.textStrong : colors.textMuted,
              borderRadius: radii.sm,
            }}
          >
            {s.label}
          </button>
        )
      })}
    </div>
  )
}

function ActivityContent() {
  const { agents, loading: agentsLoading, error: agentsError } = useAgents()
  const { agentId, invalidAgent } = useSelectedAgent(agents)
  const isOss = useEdition().edition === 'oss'
  const [segment, setSegment] = useState<Segment>('events')
  const [showTimeline, setShowTimeline] = useState(false)

  // Hooks must run unconditionally; they no-op on a null agent.
  const events = useAgentEvents(agentId)
  const { stats, pollError } = useAgentStats(agentId)

  if (agentsLoading) return <Skeleton rows={6} />

  if (agentsError) {
    return <ErrorState message={agentsError} />
  }

  // Fresh install: nothing to scope any tab to yet.
  if (agents.length === 0) {
    return (
      <>
        <PageHeader
          title="Activity"
          description="Everything your agents record, as it happens."
        />
        {isOss ? (
          <>
            <p className="text-sm mb-4" style={{ color: colors.textMuted }}>
              Welcome to ZizkaDB. Create an API key, add it to your app, and your
              first agent appears here the moment it logs an event.
            </p>
            <div className="mb-5">
              <CreateApiKeyCard />
            </div>
            <GettingStartedChecklist />
          </>
        ) : (
          <>
            <EmptyState
              title="No agents yet"
              description="An agent is created automatically the first time it logs an event. Follow the steps below to send your first one."
            />
            <div className="mt-5">
              <GettingStartedChecklist />
            </div>
          </>
        )}
      </>
    )
  }

  return (
    <>
      {/* Title + segment switcher share one row so the workspace starts higher. */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold leading-tight" style={{ color: colors.textStrong }}>
            Activity
          </h1>
          {pollError && (
            <p className="text-xs mt-1" style={{ color: colors.warning }}>
              Live stats temporarily unavailable — showing last known counts.
            </p>
          )}
          {stats && (
            <p className="text-xs mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5" style={{ color: colors.textMuted }}>
              <span><span style={{ color: colors.text }}>{stats.total_events.toLocaleString()}</span> events</span>
              <span aria-hidden style={{ color: colors.border }}>·</span>
              <span><span style={{ color: colors.text }}>{stats.unique_event_types}</span> types</span>
              <span aria-hidden style={{ color: colors.border }}>·</span>
              <span><span style={{ color: colors.text }}>{stats.sessions}</span> sessions</span>
              {stats.last_event && (
                <>
                  <span aria-hidden style={{ color: colors.border }}>·</span>
                  <span>last event {formatDistanceToNow(new Date(stats.last_event), { addSuffix: true })}</span>
                </>
              )}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {agentId && (
            <button
              type="button"
              aria-expanded={showTimeline}
              aria-controls="event-timeline"
              onClick={() => setShowTimeline((v) => !v)}
              className="inline-flex items-center gap-2 text-xs font-semibold px-3 py-1.5"
              style={{
                background: showTimeline ? colors.info : colors.infoBg,
                color: showTimeline ? '#fff' : colors.text,
                border: `1px solid ${colors.info}`,
                borderRadius: radii.md,
              }}
            >
              <ScatterChart size={14} aria-hidden />
              {showTimeline ? 'Hide event timeline' : 'Event timeline'}
            </button>
          )}
          <SegmentedControl value={segment} onChange={setSegment} />
        </div>
      </div>

      {showTimeline && agentId && (
        <div id="event-timeline">
          <EventTimelinePanel key={agentId} agentId={agentId} />
        </div>
      )}

      {invalidAgent && (
        <div
          className="mb-4 px-3 py-2.5 text-sm"
          style={{
            background: colors.warningBg,
            border: `1px solid ${colors.warning}40`,
            borderRadius: radii.md,
            color: colors.warning,
          }}
          role="status"
        >
          That agent no longer exists. Showing <span className="font-mono">{agentId}</span> instead.
        </div>
      )}

      {segment === 'events' && <EventsSegment events={events} stats={stats} />}
      {segment === 'timeline' && <SessionTimelineSegment />}
      {segment === 'sessions' && <SessionsSegment agentId={agentId} />}
      {segment === 'timetravel' && <TimeTravelSegment agentId={agentId} />}
    </>
  )
}

export default function ActivityPage() {
  // useSelectedAgent reads useSearchParams — Next.js requires a Suspense
  // boundary or the route bails out of static rendering at build time.
  return (
    <Suspense fallback={<Skeleton rows={6} />}>
      <ActivityContent />
    </Suspense>
  )
}

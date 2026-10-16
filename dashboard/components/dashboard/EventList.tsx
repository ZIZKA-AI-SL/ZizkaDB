'use client'

import { memo, useMemo } from 'react'
import { format } from 'date-fns'
import { AlertCircle, ChevronDown, ChevronRight } from 'lucide-react'
import type { AgentEvent } from '@/lib/api'
import { eventActionName, eventColor, eventLabel, groupBySession, isErrorEvent } from '@/lib/events'
import { colors, radii } from '@/lib/design-tokens'

export function EventDot({ type, size = 'md' }: { type: string; size?: 'sm' | 'md' }) {
  const d = size === 'sm' ? 6 : 8
  return (
    <span
      aria-hidden="true"
      className="rounded-full shrink-0"
      style={{ width: d, height: d, background: eventColor(type) }}
    />
  )
}

interface EventRowProps {
  event: AgentEvent
  selected: boolean
  expanded: boolean
  onSelect: (e: AgentEvent) => void
  onToggleExpand: (id: string) => void
}

/**
 * One event. Memoised because a 50-row list re-renders on every 10s poll and
 * only the selected/expanded rows actually change.
 */
const EventRow = memo(function EventRow({
  event,
  selected,
  expanded,
  onSelect,
  onToggleExpand,
}: EventRowProps) {
  const preview = JSON.stringify(event.data ?? {}).slice(0, 90)
  const actionName = eventActionName(event)

  return (
    <div>
      <button
        onClick={() => onSelect(event)}
        aria-expanded={selected}
        className={`w-full text-left px-4 py-3 ${selected ? '' : 'row-hover'}`}
        style={{
          background: selected ? colors.successBg : colors.surface,
          border: `1px solid ${selected ? '#22c55e40' : colors.border}`,
          borderRadius: selected ? `${radii.md}px ${radii.md}px 0 0` : radii.md,
        }}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <EventDot type={event.event} />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span
                  className="text-sm font-mono truncate"
                  style={{ color: colors.text }}
                  title={eventLabel(event)}
                >
                  {event.event}
                  {actionName && (
                    <span style={{ color: colors.textMuted }}> · {actionName}</span>
                  )}
                </span>
                {event.parent_id && (
                  <span
                    className="text-xs px-1.5 py-0.5 rounded shrink-0"
                    style={{ background: colors.successBg, color: colors.success }}
                    title="This event records the event that caused it"
                  >
                    causal
                  </span>
                )}
                {isErrorEvent(event.event) && (
                  <AlertCircle size={12} style={{ color: colors.danger }} aria-label="error" />
                )}
              </div>
              <div
                className="text-xs font-mono mt-0.5 truncate"
                style={{ color: colors.textFaint }}
              >
                {preview}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0 text-xs font-mono" style={{ color: colors.textFaint }}>
            <span>#{event.sequence_no}</span>
            <span>{format(new Date(event.timestamp), 'HH:mm:ss')}</span>
          </div>
        </div>
      </button>

      {selected && (
        <div
          style={{
            background: colors.bg,
            border: `1px solid ${colors.border}`,
            borderTop: 'none',
            borderRadius: `0 0 ${radii.md}px ${radii.md}px`,
          }}
        >
          <button
            onClick={() => onToggleExpand(event.event_id)}
            aria-expanded={expanded}
            className="w-full flex items-center gap-2 px-4 py-2 text-xs"
            style={{ color: colors.textMuted }}
          >
            {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            Full payload
          </button>
          {expanded && (
            <pre
              className="px-4 pb-3 text-xs overflow-auto"
              style={{ color: '#86efac', maxHeight: 240 }}
            >
              {JSON.stringify(event.data, null, 2)}
            </pre>
          )}
          <div
            className="px-4 pb-2.5 flex flex-wrap gap-x-4 gap-y-1 text-xs"
            style={{ color: colors.textFaint }}
          >
            <span>
              id: <span className="font-mono">{event.event_id.slice(0, 18)}…</span>
            </span>
            {event.session_id && (
              <span>
                session: <span className="font-mono">{event.session_id.slice(0, 12)}…</span>
              </span>
            )}
            <span>{format(new Date(event.timestamp), 'yyyy-MM-dd HH:mm:ss.SSS')}</span>
          </div>
        </div>
      )}
    </div>
  )
})

/**
 * Events grouped by session. Events with no `session_id` fall into a single
 * un-headed group so they still render.
 */
export function EventList({
  events,
  selected,
  expanded,
  onSelect,
  onToggleExpand,
}: {
  events: AgentEvent[]
  selected: AgentEvent | null
  expanded: string | null
  onSelect: (e: AgentEvent) => void
  onToggleExpand: (id: string) => void
}) {
  const grouped = useMemo(() => groupBySession(events), [events])

  return (
    <div className="space-y-4">
      {grouped.map(({ sessionId, events: evs }) => (
        <div key={sessionId ?? '__none__'}>
          {sessionId && (
            <div className="flex items-center gap-2 mb-2">
              <span
                className="text-xs font-mono px-2 py-0.5 rounded shrink-0"
                style={{
                  background: colors.surfaceHover,
                  color: colors.textMuted,
                  border: `1px solid ${colors.borderStrong}`,
                }}
                title={sessionId}
              >
                session {sessionId.slice(0, 14)}…
              </span>
              <div className="flex-1 h-px" style={{ background: colors.border }} />
              <span className="text-xs shrink-0" style={{ color: colors.textFaint }}>
                {evs.length} events
              </span>
            </div>
          )}
          <div className="space-y-1">
            {evs.map((ev) => (
              <EventRow
                key={ev.event_id}
                event={ev}
                selected={selected?.event_id === ev.event_id}
                expanded={expanded === ev.event_id}
                onSelect={onSelect}
                onToggleExpand={onToggleExpand}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

'use client'

import { useState } from 'react'
import type { AgentEvent, TimelineEvent } from '@/lib/api'
import { colors, radii } from '@/lib/design-tokens'
import {
  GROUP_LIST_MAX,
  canZoomInto,
  formatClockSeconds,
  formatDateTime,
  typeColor,
  type TimeRange,
  type TimelineGroup,
} from '@/lib/timeline'
import { LoadingLine } from '@/components/ui'
import { useGroupEvents } from '@/hooks/useAgentTimeline'
import { eventPreview } from './EventInspector'

const PAGE = 50

function asTimelineEvent(e: AgentEvent): TimelineEvent {
  return {
    event_id: e.event_id,
    timestamp: e.timestamp,
    event: e.event,
    session_id: e.session_id,
    parent_id: e.parent_id,
    is_error: false,
  }
}

interface Props {
  agentId: string
  group: TimelineGroup
  /** For an errors group: the window's errors (already loaded with the timeline). */
  errors?: TimelineEvent[]
  range: TimeRange
  onSelect: (event: TimelineEvent) => void
  onZoom: (range: TimeRange) => void
}

/** The events behind a grouped dot; click one to see everything about it. */
export function GroupEvents({ agentId, group, errors, range, onSelect, onZoom }: Props) {
  // A new group remounts this component (keyed by the panel), so the page resets.
  const [limit, setLimit] = useState(PAGE)
  const { events, loading, error } = useGroupEvents(agentId, group.errors ? null : group, limit)
  const color = group.errors ? colors.danger : typeColor(group.type)
  const shown = events?.length ?? 0
  if (group.errors) {
    const list = (errors ?? []).filter((e) => {
      const t = new Date(e.timestamp).getTime()
      return t >= group.from && t < group.to
    })
    return (
      <div className="flex flex-col gap-3 min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className="font-mono text-xs px-2 py-0.5"
            style={{ color, background: `${color}22`, border: `1px solid ${color}66`, borderRadius: radii.sm }}
          >
            errors
          </span>
          <span className="text-sm font-semibold" style={{ color: colors.textStrong }}>
            {group.count.toLocaleString()} errors
          </span>
        </div>
        <p className="text-xs" style={{ color: colors.textMuted }}>
          {formatDateTime(group.from)} → {formatClockSeconds(group.to)}. Click one to see where it happened and why.
        </p>
        <ul className="flex flex-col gap-1 overflow-y-auto pr-1" style={{ maxHeight: 520 }} aria-label="errors">
          {list.map((e) => (
            <li key={e.event_id}>
              <button
                type="button"
                onClick={() => onSelect(e)}
                className="w-full text-left px-2 py-1.5 flex flex-col gap-0.5"
                style={{ background: colors.surface, border: `1px solid ${colors.danger}55`, borderRadius: radii.sm }}
              >
                <span className="font-mono text-xs" style={{ color: colors.danger }}>{e.event}</span>
                <span className="font-mono text-xs" style={{ color: colors.textMuted }}>
                  {formatClockSeconds(new Date(e.timestamp).getTime())}{e.session_id ? ` · ${e.session_id}` : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
        {list.length < group.count && (
          <p className="text-xs" style={{ color: colors.textFaint }}>
            Showing {list.length.toLocaleString()} of {group.count.toLocaleString()}; zoom in to see the rest.
          </p>
        )}
      </div>
    )
  }
  const more = Math.min(group.count, GROUP_LIST_MAX) > shown

  return (
    <div className="flex flex-col gap-3 min-w-0">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className="font-mono text-xs px-2 py-0.5"
          style={{ color, background: `${color}22`, border: `1px solid ${color}66`, borderRadius: radii.sm }}
        >
          {group.type}
        </span>
        <span className="text-sm font-semibold" style={{ color: colors.textStrong }}>
          {group.count.toLocaleString()} events
        </span>
      </div>
      <p className="text-xs" style={{ color: colors.textMuted }}>
        {formatDateTime(group.from)} → {formatClockSeconds(group.to)}. Newest first; click one to see where it
        happened, why, and everything recorded about it.
      </p>
      {canZoomInto(range) && (
        <button
          type="button"
          className="self-start text-xs font-medium px-3 py-1.5"
          style={{ background: colors.surfaceHover, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radii.md }}
          onClick={() => onZoom({ from: group.from, to: group.to })}
        >
          Zoom in to this time
        </button>
      )}

      {error && <p className="text-xs" style={{ color: colors.danger }}>{error}</p>}
      {!events && loading && <LoadingLine label="Loading events…" />}

      {events && (
        <ul className="flex flex-col gap-1 overflow-y-auto pr-1" style={{ maxHeight: 520 }} aria-label={`${group.type} events`}>
          {events.map((e) => {
            const preview = eventPreview(e)
            return (
              <li key={e.event_id}>
                <button
                  type="button"
                  onClick={() => onSelect(asTimelineEvent(e))}
                  className="w-full text-left px-2 py-1.5 flex flex-col gap-0.5"
                  style={{ background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: radii.sm }}
                >
                  <span className="font-mono text-xs" style={{ color: colors.textMuted }}>
                    {formatClockSeconds(new Date(e.timestamp).getTime())} · #{e.sequence_no}
                  </span>
                  {preview && (
                    <span className="text-xs truncate" style={{ color: colors.text }}>{preview}</span>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
      )}

      {events && more && (
        <button
          type="button"
          disabled={loading}
          className="self-start text-xs font-medium px-3 py-1.5"
          style={{ background: colors.surfaceHover, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radii.md }}
          onClick={() => setLimit((n) => Math.min(n + PAGE, GROUP_LIST_MAX))}
        >
          {loading ? 'Loading…' : `Show ${Math.min(PAGE, Math.min(group.count, GROUP_LIST_MAX) - shown)} more`}
        </button>
      )}
      {events && !more && group.count > shown && (
        <p className="text-xs" style={{ color: colors.textFaint }}>
          Showing the newest {shown.toLocaleString()} of {group.count.toLocaleString()}. Narrow the window to see the rest.
        </p>
      )}
    </div>
  )
}

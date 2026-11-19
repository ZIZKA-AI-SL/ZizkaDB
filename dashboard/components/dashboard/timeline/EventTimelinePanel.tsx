'use client'

import { useMemo, useState, type FormEvent } from 'react'
import type { TimelineEvent } from '@/lib/api'
import { colors, radii } from '@/lib/design-tokens'
import {
  DAY,
  PRESETS,
  activePreset,
  clampRange,
  currentTime,
  formatDateTime,
  laneOrder,
  presetRange,
  toLocalInput,
  typeColor,
  type PresetId,
  type TimeRange,
  type TimelineGroup,
} from '@/lib/timeline'
import { useAgentTimeline } from '@/hooks/useAgentTimeline'
import { ErrorState, Skeleton } from '@/components/ui'
import { TimelineChart } from './TimelineChart'
import { SpikeChart } from './SpikeChart'
import { OverviewStrip } from './OverviewStrip'
import { EventInspector } from './EventInspector'
import { GroupEvents } from './GroupEvents'

const OVERVIEW_DAYS = 30

/**
 * The event timeline: every event of one agent against time, one colour per
 * event type and errors in red. Choose the window with the presets, exact
 * dates, the overview strip, or by dragging across the graph; click a dot to
 * see where it happened and everything recorded about it.
 */
export function EventTimelinePanel({ agentId }: { agentId: string }) {
  const [now, setNow] = useState(currentTime)
  const [range, setRange] = useState<TimeRange>(() => presetRange('24h', currentTime()))
  const [history, setHistory] = useState<TimeRange[]>([])
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [selected, setSelected] = useState<TimelineEvent | null>(null)
  // The grouped dot whose events are listed (zoomed-out graph).
  const [group, setGroup] = useState<TimelineGroup | null>(null)
  // Spikes (events per time slot, stacked by type) or one row per type.
  const [view, setView] = useState<'spikes' | 'types'>('spikes')
  // Typed but not yet applied dates; cleared whenever the window changes.
  const [draft, setDraft] = useState<{ from?: string; to?: string }>({})
  const fromInput = draft.from ?? toLocalInput(range.from)
  const toInput = draft.to ?? toLocalInput(range.to)

  const domain = useMemo<TimeRange>(
    () => ({ from: Math.min(now - OVERVIEW_DAYS * DAY, range.from), to: Math.max(now, range.to) }),
    [now, range.from, range.to],
  )
  const overviewRange = useMemo(() => ({ from: domain.from, to: domain.to }), [domain.from, domain.to])

  const { timeline, loading, error } = useAgentTimeline(agentId, range)
  const { timeline: overview } = useAgentTimeline(agentId, overviewRange)

  const go = (next: TimeRange, remember = true) => {
    const clamped = clampRange(next, domain.from - 365 * DAY, Math.max(now, next.to))
    if (remember) setHistory((h) => [...h.slice(-19), range])
    setDraft({})
    setRange(clamped)
  }
  const pickPreset = (id: PresetId) => {
    const t = currentTime()
    setNow(t)
    go(presetRange(id, t))
  }
  const zoomOut = () => {
    if (history.length) {
      setDraft({})
      setRange(history[history.length - 1])
      setHistory((h) => h.slice(0, -1))
    } else {
      const span = range.to - range.from
      go({ from: range.from - span / 2, to: range.to + span / 2 }, false)
    }
  }
  const submitCustom = (ev: FormEvent) => {
    ev.preventDefault()
    const from = new Date(fromInput).getTime()
    const to = new Date(toInput).getTime()
    if (!Number.isNaN(from) && !Number.isNaN(to) && to > from) go({ from, to })
  }

  const lanes = useMemo(
    () => (timeline ? laneOrder(timeline.types).filter((t) => !hidden.has(t)) : []),
    [timeline, hidden],
  )
  const preset = activePreset(range, now)
  const errorChats = timeline
    ? new Set(timeline.errors.map((e) => e.session_id).filter(Boolean)).size
    : 0

  const toggle = (type: string) =>
    setHidden((h) => {
      const next = new Set(h)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })

  const inputStyle = {
    background: colors.surfaceAlt,
    border: `1px solid ${colors.border}`,
    borderRadius: radii.sm,
    color: colors.text,
    colorScheme: 'dark' as const,
  }

  return (
    <section
      aria-label="Event timeline"
      className="mb-4 grid gap-0 overflow-hidden lg:grid-cols-[minmax(0,1fr)_340px]"
      style={{ background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: radii.lg }}
    >
      <div className="flex flex-col gap-3 p-4 min-w-0" style={{ borderRight: `1px solid ${colors.border}` }}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="text-sm font-semibold" style={{ color: colors.textStrong }}>Event timeline</div>
            <div className="text-xs" style={{ color: colors.textFaint }}>
              {formatDateTime(range.from)} → {formatDateTime(range.to)}
              {loading && ' · loading…'}
            </div>
          </div>
          <div
            role="group"
            aria-label="Time range"
            className="inline-flex p-0.5"
            style={{ background: colors.surfaceAlt, border: `1px solid ${colors.border}`, borderRadius: radii.md }}
          >
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                aria-pressed={preset === p.id}
                onClick={() => pickPreset(p.id)}
                className="text-xs font-medium px-2.5 py-1"
                style={{
                  background: preset === p.id ? colors.surfaceHover : 'transparent',
                  color: preset === p.id ? colors.textStrong : colors.textMuted,
                  borderRadius: radii.sm,
                }}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <form onSubmit={submitCustom} className="flex flex-wrap items-center gap-2 text-xs" style={{ color: colors.textMuted }}>
          <label htmlFor="timeline-from">From</label>
          <input id="timeline-from" type="datetime-local" value={fromInput} onChange={(e) => setDraft((d) => ({ ...d, from: e.target.value }))} className="px-2 py-1 font-mono" style={inputStyle} />
          <label htmlFor="timeline-to">to</label>
          <input id="timeline-to" type="datetime-local" value={toInput} onChange={(e) => setDraft((d) => ({ ...d, to: e.target.value }))} className="px-2 py-1 font-mono" style={inputStyle} />
          <button type="submit" className="px-2.5 py-1 font-medium" style={{ ...inputStyle, background: colors.surfaceHover }}>
            Show range
          </button>
          <button type="button" onClick={zoomOut} className="px-2.5 py-1 font-medium" style={{ ...inputStyle, background: colors.surfaceHover }}>
            Zoom out
          </button>
        </form>

        {error && <ErrorState message={error} />}
        {!timeline && !error && <Skeleton rows={5} />}

        {timeline && (
          <>
            <div className="flex flex-wrap gap-1.5">
              {laneOrder(timeline.types).map((type) => {
                const info = timeline.types.find((t) => t.event === type)!
                const off = hidden.has(type)
                return (
                  <button
                    key={type}
                    type="button"
                    aria-pressed={!off}
                    onClick={() => toggle(type)}
                    className="inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-mono"
                    style={{
                      opacity: off ? 0.4 : 1,
                      background: colors.surfaceAlt,
                      border: `1px solid ${colors.border}`,
                      borderRadius: radii.full,
                      color: colors.text,
                    }}
                  >
                    <span aria-hidden className="rounded-full" style={{ width: 8, height: 8, background: typeColor(type) }} />
                    {type}
                    <span style={{ color: colors.textFaint }}>{info.count.toLocaleString()}</span>
                  </button>
                )
              })}
            </div>

            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" style={{ color: colors.textMuted }}>
              <span><b style={{ color: colors.text }}>{timeline.total.toLocaleString()}</b> events</span>
              <span>
                <b style={{ color: timeline.error_total ? colors.danger : colors.text }}>{timeline.error_total.toLocaleString()}</b> errors
                {errorChats > 0 && <> in <b style={{ color: colors.text }}>{errorChats}</b> chats</>}
              </span>
              {view === 'types' && timeline.events === null && (
                <span>Zoomed out: events grouped by time. Errors are always shown one by one.</span>
              )}
              {timeline.errors_truncated && (
                <span style={{ color: colors.warning }}>Showing the newest {timeline.errors.length.toLocaleString()} errors; zoom in to see the rest.</span>
              )}
            </div>

            {timeline.total > 0 && (
              <div
                role="group"
                aria-label="Graph view"
                className="inline-flex self-start p-0.5"
                style={{ background: colors.surfaceAlt, border: `1px solid ${colors.border}`, borderRadius: radii.md }}
              >
                {([['spikes', 'Spikes'], ['types', 'By type']] as const).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={view === id}
                    onClick={() => setView(id)}
                    className="text-xs font-medium px-2.5 py-1"
                    style={{
                      background: view === id ? colors.surfaceHover : 'transparent',
                      color: view === id ? colors.textStrong : colors.textMuted,
                      borderRadius: radii.sm,
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}

            {timeline.total === 0 ? (
              <p className="text-sm py-6" style={{ color: colors.textMuted }}>
                No events in this window. Pick a longer range or move the window below.
              </p>
            ) : view === 'spikes' ? (
              <SpikeChart
                timeline={timeline}
                range={range}
                lanes={lanes}
                selectedGroup={group}
                onGroup={(g) => {
                  setSelected(null)
                  setGroup(g)
                }}
                onErrors={(slot) => {
                  setSelected(null)
                  setGroup({ type: 'errors', ...slot, errors: true })
                }}
                onZoom={(r) => go(r)}
              />
            ) : (
              <TimelineChart
                timeline={timeline}
                range={range}
                lanes={lanes}
                selected={selected}
                onSelect={(e) => {
                  setGroup(null)
                  setSelected(e)
                }}
                selectedGroup={group}
                onGroup={(g) => {
                  setSelected(null)
                  setGroup(g)
                }}
                onZoom={(r) => go(r)}
              />
            )}
          </>
        )}

        <div className="flex flex-col gap-1 pt-2" style={{ borderTop: `1px solid ${colors.border}` }}>
          <p className="text-xs" style={{ color: colors.textFaint }}>
            Drag the window to move it, drag its edges to resize, or drag anywhere to draw a new one. You can also drag across the graph above to zoom in.
          </p>
          <OverviewStrip overview={overview} domain={domain} range={range} onChange={(r) => go(r)} />
        </div>
      </div>

      <aside className="p-4 min-w-0" style={{ background: colors.surfaceAlt }}>
        {!selected && group ? (
          <GroupEvents
            key={`${group.type}|${group.from}|${group.to}`}
            agentId={agentId}
            group={group}
            errors={timeline?.errors}
            range={range}
            onSelect={setSelected}
            onZoom={(r) => {
              setGroup(null)
              go(r)
            }}
          />
        ) : (
          <EventInspector
            agentId={agentId}
            selected={selected}
            onZoom={(r) => go(r)}
            backLabel={selected && group ? (group.errors ? `${group.count.toLocaleString()} errors` : `${group.count.toLocaleString()} × ${group.type}`) : undefined}
            onBack={selected && group ? () => setSelected(null) : undefined}
          />
        )}
      </aside>
    </section>
  )
}

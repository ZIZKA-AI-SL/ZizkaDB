'use client'

import type { AgentEvent, TimelineEvent } from '@/lib/api'
import { colors, radii } from '@/lib/design-tokens'
import { eventLabel } from '@/lib/events'
import { formatClockSeconds, formatDateTime, typeColor, type TimeRange } from '@/lib/timeline'
import { JsonBlock, LoadingLine, MetaRow } from '@/components/ui'
import { useTimelineEventDetail } from '@/hooks/useAgentTimeline'
import { useCopyToClipboard } from '@/hooks/useCopyToClipboard'
import { agentVersionOf, firstFailure, modelOf, writerLabel } from '@/lib/responsibility'

const PREVIEW_KEYS = ['text', 'content', 'message', 'output', 'error', 'prompt', 'result']

/** A one-line preview of what an event recorded. */
export function eventPreview(event: Pick<AgentEvent, 'data'>): string {
  const data = event.data ?? {}
  for (const key of PREVIEW_KEYS) {
    const v = data[key]
    if (typeof v === 'string' && v.trim()) return v.length > 160 ? `${v.slice(0, 159)}…` : v
  }
  return ''
}

function Section({ title }: { title: string }) {
  return (
    <div className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: colors.textFaint }}>
      {title}
    </div>
  )
}

function Dot({ type, error }: { type: string; error?: boolean }) {
  return (
    <span
      aria-hidden
      className="inline-block rounded-full shrink-0"
      style={{ width: 10, height: 10, background: error ? colors.danger : typeColor(type), marginTop: 3 }}
    />
  )
}

interface Props {
  agentId: string
  selected: TimelineEvent | null
  onZoom: (range: TimeRange) => void
  /** Back to the grouped dot this event was picked from. */
  onBack?: () => void
  backLabel?: string
}

/** Everything recorded about the clicked event: where it happened, why, and its data. */
export function EventInspector({ agentId, selected, onZoom, onBack, backLabel }: Props) {
  const { chain, chat, loading, error } = useTimelineEventDetail(
    selected?.event_id ?? null,
    selected?.session_id ?? null,
  )
  const { copied, copy } = useCopyToClipboard()

  if (!selected) {
    return (
      <p className="text-sm" style={{ color: colors.textMuted }}>
        Click a dot to see where the event happened, why, and everything recorded about it. Red dots are errors.
        When the graph is zoomed out a dot can stand for many events; clicking it lists them here.
      </p>
    )
  }

  const full = chain?.chain[chain.chain.length - 1]
  const event = full && full.event_id === selected.event_id ? full : null
  const failure = chain ? firstFailure(chain.chain) : null
  const multiAgent = chain ? new Set(chain.chain.map((c) => c.agent)).size > 1 : false
  const position = chat ? chat.findIndex((e) => e.event_id === selected.event_id) : -1
  const color = selected.is_error ? colors.danger : typeColor(selected.event)
  const testCommand = selected.session_id
    ? `zizkadb test from-session ${selected.session_id} --agent ${agentId}`
    : null

  return (
    <div className="flex flex-col gap-4 min-w-0">
      {onBack && (
        <button type="button" onClick={onBack} className="self-start text-xs" style={{ color: colors.info }}>
          ← Back to {backLabel ?? 'the list'}
        </button>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <span
          className="font-mono text-xs px-2 py-0.5"
          style={{ color, background: `${color}22`, border: `1px solid ${color}66`, borderRadius: radii.sm }}
        >
          {selected.event}
        </span>
        <span className="text-sm font-semibold" style={{ color: colors.textStrong }}>
          {formatDateTime(new Date(selected.timestamp).getTime())}
        </span>
      </div>

      <div className="space-y-1.5">
        <MetaRow label="chat" value={selected.session_id ?? 'no session'} />
        {chat && position >= 0 && <MetaRow label="position" value={`step ${position + 1} of ${chat.length}`} />}
        <MetaRow label="event_id" value={selected.event_id} />
        <MetaRow label="parent_id" value={selected.parent_id ?? '—'} />
      </div>

      <div className="flex flex-wrap gap-2">
        {chat && chat.length > 0 && (
          <button
            type="button"
            className="text-xs font-medium px-3 py-1.5"
            style={{ background: colors.info, color: '#fff', borderRadius: radii.md }}
            onClick={() => {
              const first = new Date(chat[0].timestamp).getTime()
              const last = new Date(chat[chat.length - 1].timestamp).getTime()
              onZoom({ from: first - 60_000, to: last + 60_000 })
            }}
          >
            Zoom to this chat
          </button>
        )}
        <button
          type="button"
          className="text-xs font-medium px-3 py-1.5"
          style={{ background: colors.surfaceHover, color: colors.text, border: `1px solid ${colors.border}`, borderRadius: radii.md }}
          onClick={() => copy(selected.event_id)}
        >
          {copied ? 'Copied' : 'Copy event id'}
        </button>
      </div>

      {loading && <LoadingLine label="Loading the event…" />}
      {error && (
        <p className="text-xs" style={{ color: colors.warning }}>
          {error}
        </p>
      )}

      {event && (
        <div className="flex flex-col gap-1.5">
          <Section title="Responsibility" />
          <MetaRow label="agent" value={event.agent} />
          <MetaRow label="written by" value={writerLabel(event.metadata)} />
          {modelOf(event) && <MetaRow label="model" value={modelOf(event)} />}
          {agentVersionOf(event.metadata) && <MetaRow label="agent version" value={agentVersionOf(event.metadata)} />}
          {failure && (
            <p className="text-xs mt-1" style={{ color: colors.text }}>
              <span style={{ color: colors.danger, fontWeight: 600 }}>First went wrong:</span>{' '}
              <span className="font-mono">{failure.event}</span> in agent{' '}
              <span className="font-mono">{failure.agent}</span>
              {failure.event_id !== selected.event_id && <> at {formatClockSeconds(new Date(failure.timestamp).getTime())}</>}
              {' · '}
              {writerLabel(failure.metadata)}
              {modelOf(failure) && <> · {modelOf(failure)}</>}
            </p>
          )}
        </div>
      )}

      {chain && (
        <div className="flex flex-col gap-2">
          <Section title="Why it happened" />
          <ol className="flex flex-col gap-2">
            {chain.chain.map((c) => (
              <li key={c.event_id} className="flex gap-2.5 text-xs">
                <Dot type={c.event} error={c.event_id === selected.event_id && selected.is_error} />
                <div className="min-w-0">
                  {multiAgent && (
                    <span className="font-mono" style={{ color: colors.textMuted }}>{c.agent} · </span>
                  )}
                  <span className="font-mono" style={{ color: colors.text }}>{eventLabel(c)}</span>{' '}
                  <span style={{ color: colors.textFaint }}>{formatClockSeconds(new Date(c.timestamp).getTime())}</span>
                  {failure && c.event_id === failure.event_id && (
                    <span className="ml-1.5 text-[10px] font-semibold uppercase" style={{ color: colors.danger }}>first failure</span>
                  )}
                  {eventPreview(c) && (
                    <div className="break-words" style={{ color: colors.textMuted }}>{eventPreview(c)}</div>
                  )}
                </div>
              </li>
            ))}
          </ol>
          {chain.continues_in && (
            <p className="text-xs" style={{ color: colors.warning }}>
              The cause continues in agent <span className="font-mono">{chain.continues_in.agent}</span> (event{' '}
              <span className="font-mono">{chain.continues_in.event_id}</span>), which this key can’t read. Open it from a dashboard login to see the whole chain.
            </p>
          )}
          {chain.orphan && (
            <p className="text-xs" style={{ color: colors.textFaint }}>
              This event has no recorded cause (no parent_id).
            </p>
          )}
        </div>
      )}

      {chat && chat.length > 0 && (
        <div className="flex flex-col gap-2">
          <Section title="In the chat" />
          <ol
            className="flex flex-col gap-1.5 p-2.5 overflow-auto"
            style={{ maxHeight: 260, background: colors.surfaceAlt, border: `1px solid ${colors.border}`, borderRadius: radii.md }}
          >
            {chat.map((c, i) => {
              const here = c.event_id === selected.event_id
              const err = typeColor(c.event) === colors.danger || (here && selected.is_error)
              return (
                <li
                  key={c.event_id}
                  className="flex gap-2 text-xs px-2 py-1.5"
                  aria-current={here ? 'step' : undefined}
                  style={{
                    borderRadius: radii.sm,
                    background: here ? `${err ? colors.danger : colors.info}1f` : 'transparent',
                    border: here ? `1px solid ${err ? colors.danger : colors.info}` : '1px solid transparent',
                  }}
                >
                  <span className="font-mono shrink-0" style={{ color: colors.textFaint, minWidth: 18 }}>{i + 1}</span>
                  <Dot type={c.event} error={err} />
                  <div className="min-w-0">
                    <span className="font-mono" style={{ color: err ? colors.danger : colors.text }}>{c.event}</span>
                    {eventPreview(c) && (
                      <div className="break-words" style={{ color: colors.textMuted }}>{eventPreview(c)}</div>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>
        </div>
      )}

      {event && (
        <div className="flex flex-col gap-2">
          <Section title="Recorded data" />
          <JsonBlock value={event.data} maxHeight={240} />
        </div>
      )}

      {testCommand && (
        <div className="flex flex-col gap-1.5">
          <Section title="Turn this chat into a test" />
          <code
            className="text-xs font-mono px-2.5 py-2 break-all"
            style={{ background: colors.surfaceAlt, border: `1px solid ${colors.border}`, borderRadius: radii.md, color: colors.text }}
          >
            {testCommand}
          </code>
        </div>
      )}
    </div>
  )
}

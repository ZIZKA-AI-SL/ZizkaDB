'use client'

import { format } from 'date-fns'
import { ArrowRight, GitBranch } from 'lucide-react'
import type { AgentEvent, WhyChain } from '@/lib/api'
import { colors, radii } from '@/lib/design-tokens'
import { JsonBlock, LoadingLine, MetaRow } from '@/components/ui'
import { eventLabel } from '@/lib/events'
import { EventDot } from './EventList'

export type PanelTab = 'data' | 'why'

function PanelTabBtn({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      role="tab"
      aria-selected={active}
      className="flex-1 px-3 py-2 text-xs font-medium transition"
      style={{
        background: active ? colors.surfaceHover : 'transparent',
        color: active ? colors.textStrong : colors.textMuted,
      }}
    >
      {label}
    </button>
  )
}

function DataPanel({ event }: { event: AgentEvent }) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <EventDot type={event.event} />
        <span className="font-mono font-semibold text-sm" style={{ color: colors.textStrong }}>
          {eventLabel(event)}
        </span>
      </div>
      <JsonBlock value={event.data} maxHeight={280} />
      <div className="space-y-2 mt-3">
        <MetaRow label="event_id" value={event.event_id} />
        <MetaRow label="sequence" value={`#${event.sequence_no}`} />
        <MetaRow
          label="timestamp"
          value={format(new Date(event.timestamp), 'yyyy-MM-dd HH:mm:ss.SSS')}
        />
        <MetaRow label="session_id" value={event.session_id ?? '—'} />
        <MetaRow label="parent_id" value={event.parent_id ?? '—'} />
        {event.index_status && (
          <MetaRow label="index_status" value={event.index_status} />
        )}
      </div>
    </div>
  )
}

function WhyPanel({ chain }: { chain: WhyChain }) {
  const incomplete =
    chain.orphan || chain.depth_truncated || chain.scoped_agent_limited || chain.chain_complete === false

  return (
    <div>
      {incomplete && (
        <div
          className="mb-3 px-3 py-2 rounded text-xs"
          style={{ background: colors.warningBg, color: colors.warning }}
        >
          {chain.orphan && 'Orphan event — missing parent_id. '}
          {chain.depth_truncated && 'Depth limit reached — chain may continue above. '}
          {chain.scoped_agent_limited && 'Agent-scoped key — cross-agent links hidden. '}
        </div>
      )}
      <div className="flex items-center gap-2 mb-4">
        <GitBranch size={13} style={{ color: colors.success }} />
        <span className="text-xs font-medium" style={{ color: colors.text }}>
          {chain.chain_length} event{chain.chain_length !== 1 ? 's' : ''} in causal chain
        </span>
      </div>
      <ol className="space-y-0">
        {chain.chain.map((e, i) => (
          <li key={e.event_id} className="flex gap-3">
            <div className="flex flex-col items-center">
              <EventDot type={e.event} />
              {i < chain.chain.length - 1 && (
                <div
                  className="w-px flex-1 my-1"
                  style={{ background: colors.borderStrong, minHeight: 16 }}
                />
              )}
            </div>
            <div className="pb-3 min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span
                  className="text-xs font-mono font-medium truncate"
                  style={{ color: colors.text }}
                >
                  {eventLabel(e)}
                </span>
                {i === 0 && (
                  <span
                    className="text-xs px-1.5 py-0.5 rounded shrink-0"
                    style={{ background: colors.successBg, color: colors.success }}
                  >
                    root
                  </span>
                )}
                {i === chain.chain.length - 1 && (
                  <span
                    className="text-xs px-1.5 py-0.5 rounded shrink-0"
                    style={{ background: colors.dangerBg, color: colors.danger }}
                  >
                    selected
                  </span>
                )}
              </div>
              <div className="text-xs font-mono truncate mt-0.5" style={{ color: colors.textFaint }}>
                {JSON.stringify(e.data ?? {}).slice(0, 55)}
              </div>
              <div className="flex items-center gap-2 mt-0.5">
                <span className="text-xs" style={{ color: colors.textFaint }}>
                  {format(new Date(e.timestamp), 'HH:mm:ss.SSS')}
                </span>
                {i < chain.chain.length - 1 && (
                  <ArrowRight size={10} style={{ color: colors.borderStrong }} />
                )}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}

/**
 * Side panel for the selected event: raw payload, or the causal chain that led
 * to it (`db.why()`).
 */
export function EventDetailPanel({
  event,
  tab,
  onTabChange,
  whyChain,
  whyLoading,
  whyError,
}: {
  event: AgentEvent
  tab: PanelTab
  onTabChange: (t: PanelTab) => void
  whyChain: WhyChain | null
  whyLoading: boolean
  whyError: string | null
}) {
  return (
    <div className="w-full lg:w-80 shrink-0">
      <div
        role="tablist"
        aria-label="Event detail"
        className="flex mb-2 overflow-hidden"
        style={{
          background: colors.surface,
          border: `1px solid ${colors.border}`,
          borderRadius: radii.lg,
        }}
      >
        <PanelTabBtn label="Data" active={tab === 'data'} onClick={() => onTabChange('data')} />
        <PanelTabBtn
          label="Why? (causal)"
          active={tab === 'why'}
          onClick={() => onTabChange('why')}
        />
      </div>

      <div
        className="p-4 lg:sticky lg:top-4 overflow-auto"
        style={{
          background: colors.surface,
          border: `1px solid ${colors.border}`,
          borderRadius: radii.lg,
          maxHeight: '80vh',
        }}
      >
        {tab === 'data' ? (
          <DataPanel event={event} />
        ) : whyLoading ? (
          <LoadingLine label="Tracing causal chain…" />
        ) : whyError ? (
          <p className="text-sm" style={{ color: colors.danger }}>
            {whyError}
          </p>
        ) : whyChain ? (
          <WhyPanel chain={whyChain} />
        ) : (
          <p className="text-sm" style={{ color: colors.textMuted }}>
            No causal chain recorded for this event.
          </p>
        )}
      </div>
    </div>
  )
}

'use client'

import { useMemo } from 'react'
import { Activity, RefreshCw, TrendingDown, TrendingUp } from 'lucide-react'
import type { AgentBaseline, BaselineChange, BaselineWindow } from '@/lib/api'
import { eventColor, topN } from '@/lib/events'
import { colors, radii } from '@/lib/design-tokens'
import { ActionsTable } from './ActionsTable'
import { EmptyState } from '@/components/ui'

/**
 * Drift presentation. The verdict thresholds and every number shown here come
 * straight from the backend — this file only decides how they look.
 */
const VERDICT_META = {
  stable: {
    color: colors.success,
    bg: colors.successBg,
    label: 'Stable',
    desc: (agent: string) => `${agent} is behaving consistently with its baseline.`,
  },
  minor_drift: {
    color: '#eab308',
    bg: '#1a1500',
    label: 'Minor drift',
    desc: () => 'Small shifts in event distribution. Worth a quick look.',
  },
  noticeable_drift: {
    color: '#f97316',
    bg: '#1a0f00',
    label: 'Noticeable drift',
    desc: () => 'Recent sessions are diverging from baseline. Investigate before users notice.',
  },
  significant_drift: {
    color: '#ef4444',
    bg: colors.dangerBg,
    label: 'Significant drift',
    desc: () => 'Recent sessions look meaningfully different. Likely a regression.',
  },
} as const

function Mini({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'bad' | 'neutral' }) {
  return (
    <div
      className="p-2.5"
      style={{ background: colors.bg, border: `1px solid ${colors.border}`, borderRadius: radii.sm }}
    >
      <div className="text-xs mb-0.5" style={{ color: colors.textFaint }}>
        {label}
      </div>
      <div
        className="text-sm font-semibold font-mono"
        style={{ color: tone === 'bad' ? colors.danger : colors.text }}
      >
        {value}
      </div>
    </div>
  )
}

function SubStat({
  label,
  value,
  sub,
  tone = 'neutral',
}: {
  label: string
  value: string
  sub: string
  tone?: 'good' | 'bad' | 'neutral'
}) {
  const c = tone === 'good' ? colors.success : tone === 'bad' ? colors.danger : colors.text
  return (
    <div
      className="p-3"
      style={{ background: colors.bg, border: `1px solid ${colors.border}`, borderRadius: radii.md }}
    >
      <div className="text-xs mb-1" style={{ color: colors.textFaint }}>
        {label}
      </div>
      <div className="text-base font-semibold font-mono" style={{ color: c }}>
        {value}
      </div>
      <div className="text-xs mt-0.5" style={{ color: colors.textFaint }}>
        {sub}
      </div>
    </div>
  )
}

function DistRow({ label, pct, color }: { label: string; pct: number; color: string }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-0.5 gap-2">
        <span className="text-xs font-mono truncate" style={{ color: colors.text }} title={label}>
          {label}
        </span>
        <span className="text-xs font-mono shrink-0" style={{ color: colors.textMuted }}>
          {pct.toFixed(1)}%
        </span>
      </div>
      <div className="h-1 rounded overflow-hidden" style={{ background: colors.bg }}>
        <div
          className="h-full rounded"
          style={{ width: `${Math.min(pct, 100)}%`, background: color, opacity: 0.7 }}
        />
      </div>
    </div>
  )
}

function ChangeRow({ change }: { change: BaselineChange }) {
  const isUp = change.delta_pp > 0
  const Icon = isUp ? TrendingUp : TrendingDown
  const color = Math.abs(change.delta_pp) > 10 ? '#f97316' : colors.textMuted

  const [scope, ...rest] = change.metric.split('.')
  const detail = rest.join('.')
  const scopeLabel =
    scope === 'event_distribution' ? 'event' : scope === 'transitions' ? 'transition' :
    scope === 'actions' ? 'tool / api' : scope

  return (
    <div className="px-4 py-2.5 flex items-center gap-3">
      <Icon size={13} style={{ color }} />
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-xs uppercase tracking-wider shrink-0" style={{ color: colors.textFaint }}>
            {scopeLabel}
          </span>
          <span className="text-sm font-mono truncate" style={{ color: colors.text }} title={detail}>
            {detail}
          </span>
        </div>
        <div className="text-xs font-mono mt-0.5" style={{ color: colors.textFaint }}>
          {change.baseline.toFixed(2)}% → {change.recent.toFixed(2)}%
        </div>
      </div>
      <div className="text-sm font-semibold font-mono shrink-0" style={{ color }}>
        {isUp ? '+' : ''}
        {change.delta_pp.toFixed(2)}pp
      </div>
    </div>
  )
}

function WindowCard({
  title,
  subtitle,
  window: w,
  highlight = false,
}: {
  title: string
  subtitle: string
  window: BaselineWindow
  highlight?: boolean
}) {
  const topEvents = useMemo(() => topN(w.event_distribution, 6), [w])
  const topTransitions = useMemo(() => topN(w.transitions, 5), [w])

  return (
    <div
      style={{
        background: highlight ? colors.successBg : colors.surfaceAlt,
        border: `1px solid ${highlight ? '#22c55e30' : colors.border}`,
        borderRadius: radii.lg,
      }}
    >
      <div
        className="px-4 py-3"
        style={{ borderBottom: `1px solid ${highlight ? '#22c55e20' : colors.border}` }}
      >
        <div className="text-sm font-semibold" style={{ color: colors.textStrong }}>
          {title}
        </div>
        <div className="text-xs mt-0.5" style={{ color: colors.textMuted }}>
          {subtitle}
        </div>
      </div>
      <div className="p-4 space-y-4">
        <div className="grid grid-cols-3 gap-2">
          <Mini label="Events / session" value={w.avg_events_per_session.toFixed(1)} />
          <Mini
            label="Avg duration"
            value={
              w.avg_session_seconds < 60
                ? `${w.avg_session_seconds.toFixed(0)}s`
                : `${(w.avg_session_seconds / 60).toFixed(1)}m`
            }
          />
          <Mini
            label="Error rate"
            value={`${w.error_rate_pct.toFixed(1)}%`}
            tone={w.error_rate_pct > 5 ? 'bad' : 'neutral'}
          />
        </div>

        <div>
          <div className="text-xs font-medium mb-2" style={{ color: colors.textMuted }}>
            Event distribution
          </div>
          {topEvents.length === 0 ? (
            <div className="text-xs" style={{ color: colors.textFaint }}>
              No events
            </div>
          ) : (
            <div className="space-y-1.5">
              {topEvents.map(([type, pct]) => (
                <DistRow key={type} label={type} pct={pct} color={eventColor(type)} />
              ))}
            </div>
          )}
        </div>

        {topTransitions.length > 0 && (
          <div>
            <div className="text-xs font-medium mb-2" style={{ color: colors.textMuted }}>
              Common decision sequences (parent → child)
            </div>
            <div className="space-y-1.5">
              {topTransitions.map(([key, pct]) => (
                <DistRow key={key} label={key} pct={pct} color={colors.info} />
              ))}
            </div>
          </div>
        )}

        {(w.actions?.length ?? 0) > 0 && (
          <div>
            <div className="text-xs font-medium mb-2" style={{ color: colors.textMuted }}>
              Tools &amp; APIs called
            </div>
            <ActionsTable actions={w.actions} limit={6} bare />
          </div>
        )}
      </div>
    </div>
  )
}

function DriftHeadline({
  drift,
  agentId,
  sessions,
  recentWindow,
  onRecompute,
}: {
  drift: NonNullable<AgentBaseline['drift']>
  agentId: string
  sessions: number
  recentWindow: number
  onRecompute: () => void
}) {
  const m = VERDICT_META[drift.verdict]
  const scorePct = Math.round(drift.score * 100)

  return (
    <div
      className="p-5"
      style={{ background: m.bg, border: `1px solid ${m.color}40`, borderRadius: radii.lg }}
    >
      <div className="flex flex-wrap items-start justify-between gap-4 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <Activity size={14} style={{ color: m.color }} />
            <span className="text-xs font-bold uppercase tracking-wider" style={{ color: m.color }}>
              {m.label}
            </span>
          </div>
          <h2 className="text-lg font-semibold" style={{ color: colors.textStrong }}>
            Behavior change: <span className="font-mono">{scorePct}%</span>
            <span className="text-sm font-normal ml-2" style={{ color: colors.textMuted }}>
              (score {drift.score.toFixed(3)})
            </span>
          </h2>
          <p className="text-sm mt-1" style={{ color: colors.text }}>
            {m.desc(agentId)}
          </p>
        </div>
        <button
          onClick={onRecompute}
          className="text-xs px-3 py-1.5 transition shrink-0 inline-flex items-center gap-1.5"
          style={{
            background: colors.bg,
            color: colors.textMuted,
            border: `1px solid ${colors.borderStrong}`,
            borderRadius: radii.md,
          }}
        >
          <RefreshCw size={11} />
          Recompute
        </button>
      </div>

      <div
        className="h-2 rounded-full overflow-hidden mb-3"
        role="img"
        aria-label={`Behavior change ${scorePct} percent`}
        style={{ background: colors.bg }}
      >
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.min(scorePct, 100)}%`, background: m.color }}
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-4">
        <SubStat
          label="Sessions analyzed"
          value={String(sessions)}
          sub={`${recentWindow} recent vs ${sessions - recentWindow} prior`}
        />
        <SubStat
          label="Error rate delta"
          value={`${drift.error_rate_change_pp >= 0 ? '+' : ''}${drift.error_rate_change_pp.toFixed(2)}pp`}
          sub={
            drift.error_rate_change_pp > 0.5
              ? 'Errors up'
              : drift.error_rate_change_pp < -0.5
                ? 'Errors down'
                : 'Unchanged'
          }
          tone={
            drift.error_rate_change_pp > 0.5
              ? 'bad'
              : drift.error_rate_change_pp < -0.5
                ? 'good'
                : 'neutral'
          }
        />
        <SubStat
          label="Avg session length"
          value={`${drift.session_length_change_pct >= 0 ? '+' : ''}${drift.session_length_change_pct.toFixed(1)}%`}
          sub={
            Math.abs(drift.session_length_change_pct) > 15
              ? 'Significant shift'
              : 'Within normal range'
          }
          tone={Math.abs(drift.session_length_change_pct) > 15 ? 'bad' : 'neutral'}
        />
      </div>
    </div>
  )
}

/**
 * Renders one of the three baseline states the backend can return:
 * `insufficient_data`, `warming_up`, or a full `ok` drift report.
 */
export function BehaviorPanel({
  baseline,
  agentId,
  window: win,
  onRecompute,
}: {
  baseline: AgentBaseline
  agentId: string
  window: '24h' | '7d' | '30d' | undefined
  onRecompute: () => void
}) {
  if (baseline.status === 'insufficient_data') {
    return (
      <EmptyState
        icon={<Activity size={22} style={{ color: colors.textFaint }} />}
        title="No baseline yet"
        description={
          baseline.message ??
          'Not enough sessions recorded to establish what normal looks like for this agent.'
        }
      />
    )
  }

  if (baseline.status === 'warming_up') {
    return (
      <div className="space-y-4">
        <div
          className="p-5"
          style={{ background: '#1a0f00', border: '1px solid #f9731640', borderRadius: radii.lg }}
        >
          <div className="flex items-center gap-2 mb-2">
            <Activity size={14} style={{ color: '#f97316' }} />
            <h3 className="text-sm font-semibold" style={{ color: '#fed7aa' }}>
              Warming up
            </h3>
          </div>
          <p className="text-sm" style={{ color: '#fdba74' }}>
            {baseline.message} Drift detection kicks in once{' '}
            <span className="font-mono">{agentId}</span> has more than {baseline.recent_window}{' '}
            sessions.
          </p>
        </div>
        {baseline.recent && (
          <WindowCard
            title="Current behavior"
            subtitle={`${baseline.recent.sessions} sessions, ${baseline.recent.events} events`}
            window={baseline.recent}
            highlight
          />
        )}
      </div>
    )
  }

  // status === 'ok' — the backend guarantees drift/baseline/recent are present,
  // but guard anyway rather than assert with `!`.
  const { drift, baseline: baseW, recent: recentW, sessions, recent_window } = baseline
  if (!drift || !baseW || !recentW) {
    return (
      <EmptyState
        title="Baseline incomplete"
        description="The server reported a baseline but did not return the comparison windows. Try recomputing."
      />
    )
  }

  return (
    <div className="space-y-5">
      <DriftHeadline
        drift={drift}
        agentId={agentId}
        sessions={sessions}
        recentWindow={recent_window}
        onRecompute={onRecompute}
      />

      {drift.biggest_changes.length > 0 && (
        <div
          style={{
            background: colors.surfaceAlt,
            border: `1px solid ${colors.border}`,
            borderRadius: radii.lg,
          }}
        >
          <div
            className="px-4 py-3 flex items-center gap-2 flex-wrap"
            style={{ borderBottom: `1px solid ${colors.border}` }}
          >
            <TrendingUp size={13} style={{ color: colors.success }} />
            <span className="text-sm font-medium" style={{ color: colors.textStrong }}>
              Biggest behavioral changes
            </span>
            <span className="text-xs ml-auto" style={{ color: colors.textFaint }}>
              {win
                ? `last ${win} vs prior sessions`
                : `recent ${recent_window} vs prior ${sessions - recent_window} sessions`}
            </span>
          </div>
          <div className="divide-y" style={{ borderColor: colors.border }}>
            {drift.biggest_changes.map((c) => (
              <ChangeRow key={c.metric} change={c} />
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <WindowCard
          title="Baseline"
          subtitle={`${baseW.sessions} sessions, ${baseW.events} events (older)`}
          window={baseW}
        />
        <WindowCard
          title="Recent"
          subtitle={`${recentW.sessions} sessions, ${recentW.events} events (newest ${recent_window})`}
          window={recentW}
          highlight
        />
      </div>
    </div>
  )
}

'use client'

import { Activity, TrendingDown, TrendingUp } from 'lucide-react'
import { colors, radii } from '@/lib/design-tokens'
import type { ReportDrift, ReportSummary } from '@/lib/api'
import type { BaselineChange } from '@/lib/api'

const VERDICT: Record<ReportDrift['verdict'], { label: string; color: string }> = {
  stable: { label: 'Stable', color: colors.success },
  minor_drift: { label: 'Minor drift', color: '#eab308' },
  noticeable_drift: { label: 'Noticeable drift', color: '#f97316' },
  significant_drift: { label: 'Significant drift', color: colors.danger },
}

function ChangeRow({ change }: { change: BaselineChange }) {
  const up = change.delta_pp > 0
  const Icon = up ? TrendingUp : TrendingDown
  const color = Math.abs(change.delta_pp) > 10 ? '#f97316' : colors.textMuted
  const [scope, ...rest] = change.metric.split('.')
  const detail = rest.join('.')
  return (
    <div className="px-4 py-2 flex items-center gap-3">
      <Icon size={13} style={{ color }} />
      <div className="flex-1 min-w-0">
        <span className="text-xs uppercase tracking-wider mr-2" style={{ color: colors.textFaint }}>
          {scope === 'event_distribution' ? 'event' : scope === 'transitions' ? 'transition' : scope === 'actions' ? 'tool / api' : scope}
        </span>
        <span className="text-sm font-mono truncate" style={{ color: colors.text }} title={detail}>
          {detail}
        </span>
      </div>
      <span className="text-sm font-mono shrink-0" style={{ color }}>
        {up ? '+' : ''}
        {change.delta_pp.toFixed(2)}pp
      </span>
    </div>
  )
}

/**
 * Behavior drift for the window (honest null → "not enough history") plus the
 * error-rate footnote that discloses how errors are counted.
 */
export function ReliabilitySection({
  drift,
  summary,
}: {
  drift: ReportDrift | null
  summary: ReportSummary
}) {
  return (
    <div
      style={{ background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: radii.lg }}
    >
      <div className="px-4 py-3 flex items-center gap-2" style={{ borderBottom: `1px solid ${colors.border}` }}>
        <Activity size={14} style={{ color: colors.textMuted }} />
        <h3 className="text-sm font-medium" style={{ color: colors.textStrong }}>
          Behavior &amp; reliability
        </h3>
      </div>

      <div className="p-4 space-y-4">
        {drift ? (
          <div>
            <div className="flex items-baseline gap-2 flex-wrap">
              <span
                className="text-xs font-bold uppercase tracking-wider"
                style={{ color: VERDICT[drift.verdict].color }}
              >
                {VERDICT[drift.verdict].label}
              </span>
              <span className="text-lg font-semibold" style={{ color: colors.textStrong }}>
                {drift.behavior_change_pct}% behavior change
              </span>
            </div>
            <div className="h-2 rounded-full overflow-hidden mt-2" style={{ background: colors.bg }}>
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.min(drift.behavior_change_pct, 100)}%`,
                  background: VERDICT[drift.verdict].color,
                }}
              />
            </div>
            <p className="text-xs mt-2" style={{ color: colors.textMuted }}>
              Compared with this agent&apos;s history before the reporting period.
            </p>
            {drift.biggest_changes.length > 0 && (
              <div className="mt-3 divide-y" style={{ borderColor: colors.border, border: `1px solid ${colors.border}`, borderRadius: radii.md }}>
                {drift.biggest_changes.slice(0, 5).map((c) => (
                  <ChangeRow key={c.metric} change={c} />
                ))}
              </div>
            )}
          </div>
        ) : (
          <p className="text-sm" style={{ color: colors.textMuted }}>
            Not enough history before this period to assess behavior drift (needs 50+ prior events).
          </p>
        )}

        <p className="text-xs" style={{ color: colors.textFaint }}>
          Error rate is {summary.error_rate_pct}% ({summary.error_count} of {summary.total_events}).
          An event counts as an error when its type contains &ldquo;error&rdquo;/&ldquo;fail&rdquo; or
          its data carries an <code>error</code> field.
        </p>
      </div>
    </div>
  )
}

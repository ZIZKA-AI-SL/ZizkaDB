'use client'

import { colors, radii } from '@/lib/design-tokens'
import type { AgentAction } from '@/lib/api'

/**
 * Which tools / APIs the agent called, with call counts and error rates.
 * Rows come from the API already sorted most-called first.
 */
export function ActionsTable({
  actions,
  limit = 8,
  bare = false,
}: {
  actions: AgentAction[] | undefined
  limit?: number
  /** Render without the surrounding card (when nested in another panel). */
  bare?: boolean
}) {
  const rows = (actions ?? []).slice(0, limit)
  if (rows.length === 0) return null

  const body = (
    <table className="w-full text-xs">
      <thead>
        <tr style={{ color: colors.textMuted }}>
          <th className="text-left font-medium pb-1.5">Tool / API</th>
          <th className="text-right font-medium pb-1.5">Calls</th>
          <th className="text-right font-medium pb-1.5">Share</th>
          <th className="text-right font-medium pb-1.5">Errors</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((a) => (
          <tr key={`${a.kind}:${a.name}`} style={{ borderTop: `1px solid ${colors.border}` }}>
            <td className="py-1.5 pr-2 max-w-0 w-full">
              <div className="flex items-center gap-2 min-w-0">
                <span
                  className="uppercase tracking-wider shrink-0"
                  style={{ color: colors.textFaint, fontSize: 10 }}
                >
                  {a.kind}
                </span>
                <span className="font-mono truncate" style={{ color: colors.text }} title={a.name}>
                  {a.name}
                </span>
              </div>
            </td>
            <td className="py-1.5 text-right font-mono" style={{ color: colors.text }}>
              {a.calls}
            </td>
            <td className="py-1.5 pl-3 text-right font-mono" style={{ color: colors.textMuted }}>
              {a.share_pct.toFixed(1)}%
            </td>
            <td
              className="py-1.5 pl-3 text-right font-mono whitespace-nowrap"
              style={{ color: a.errors > 0 ? colors.danger : colors.textMuted }}
            >
              {a.errors > 0 ? `${a.errors} (${a.error_rate_pct.toFixed(1)}%)` : '0'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )

  if (bare) return body

  return (
    <div
      className="p-4 report-avoid-break"
      style={{ background: colors.surface, border: `1px solid ${colors.border}`, borderRadius: radii.lg }}
    >
      <h3 className="text-sm font-medium mb-3" style={{ color: colors.textStrong }}>
        Tools &amp; APIs called
      </h3>
      {body}
    </div>
  )
}

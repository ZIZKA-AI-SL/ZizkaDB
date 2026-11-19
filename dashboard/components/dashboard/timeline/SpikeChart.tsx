'use client'

import { useMemo, useRef, useState, type PointerEvent } from 'react'
import type { AgentTimeline } from '@/lib/api'
import { colors, radii } from '@/lib/design-tokens'
import {
  axisTicks,
  bucketLabel,
  formatDateTime,
  formatClock,
  niceMax,
  spikes,
  typeColor,
  type TimeRange,
  type TimelineGroup,
} from '@/lib/timeline'

const W = 900
const LEFT = 56
const RIGHT = 16
const TOP = 22
const PLOT = 300
const ERROR_MIN = 4

interface Props {
  timeline: AgentTimeline
  range: TimeRange
  /** Shown types, in stacking order (biggest first). */
  lanes: string[]
  selectedGroup: TimelineGroup | null
  /** A coloured part of a spike: that type's events in that time slot. */
  onGroup: (group: TimelineGroup) => void
  /** The red part of a spike: the slot's errors. */
  onErrors: (slot: { from: number; to: number; count: number }) => void
  onZoom: (range: TimeRange) => void
}

/**
 * Events against time as vertical spikes: one bar per time slot, its height
 * the number of events, stacked by event type, with the slot's errors in red
 * on top and their count above. Hover a spike for its breakdown, click a part
 * to list those events, drag across the graph to zoom in.
 */
export function SpikeChart({ timeline, range, lanes, selectedGroup, onGroup, onErrors, onZoom }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null)
  const [hover, setHover] = useState<number | null>(null)

  const plotW = W - LEFT - RIGHT
  const base = TOP + PLOT
  const height = base + 30
  const x = (t: number) => LEFT + ((t - range.from) / (range.to - range.from)) * plotW
  const bucketMs = timeline.bucket_seconds * 1000

  const rows = useMemo(() => spikes(timeline.buckets, new Set(lanes), lanes), [timeline.buckets, lanes])
  const max = niceMax(Math.max(1, ...rows.map((r) => r.total)))
  const y = (n: number) => base - (n / max) * PLOT

  const toSvgX = (ev: PointerEvent) => {
    const r = svgRef.current!.getBoundingClientRect()
    return ((ev.clientX - r.left) / r.width) * W
  }
  const toTime = (px: number) =>
    range.from + ((Math.min(W - RIGHT, Math.max(LEFT, px)) - LEFT) / plotW) * (range.to - range.from)
  const onDown = (ev: PointerEvent<SVGSVGElement>) => {
    if ((ev.target as Element).closest('[data-mark]')) return
    const px = toSvgX(ev)
    setDrag({ a: px, b: px })
    svgRef.current?.setPointerCapture(ev.pointerId)
  }
  const onMove = (ev: PointerEvent<SVGSVGElement>) => {
    if (drag) setDrag({ ...drag, b: toSvgX(ev) })
  }
  const onUp = () => {
    if (drag && Math.abs(drag.b - drag.a) > 6) {
      onZoom({ from: toTime(Math.min(drag.a, drag.b)), to: toTime(Math.max(drag.a, drag.b)) })
    }
    setDrag(null)
  }

  const hovered = hover === null ? null : rows[hover]
  // Beside the spike (right of it in the left half, left of it in the right
  // half), so the tooltip never covers the spike it describes.
  const tipAt = hovered ? (x(hovered.t + bucketMs / 2) / W) * 100 : 0
  const tipRight = tipAt < 55

  return (
    <div className="relative overflow-x-auto" onPointerLeave={() => setHover(null)}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${height}`}
        width="100%"
        style={{ minWidth: 640, display: 'block', touchAction: 'none', userSelect: 'none' }}
        role="img"
        aria-label="Event timeline: events per time slot as vertical spikes, stacked by type, errors in red"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={() => setDrag(null)}
      >
        {/* value axis */}
        {[0, max / 2, max].map((v) => (
          <g key={v}>
            <line x1={LEFT} x2={W - RIGHT} y1={y(v)} y2={y(v)} stroke={v === 0 ? colors.border : colors.surfaceHover} />
            <text x={LEFT - 8} y={y(v) + 4} textAnchor="end" fontSize={11} fill={colors.textFaint}>
              {v.toLocaleString()}
            </text>
          </g>
        ))}
        <text x={LEFT} y={TOP - 8} fontSize={11} fill={colors.textFaint}>
          events / {bucketLabel(timeline.bucket_seconds)}
        </text>

        {/* time axis */}
        {axisTicks(range).map((tk) => (
          <g key={tk.t}>
            <line x1={x(tk.t)} x2={x(tk.t)} y1={TOP} y2={base} stroke={colors.surfaceHover} strokeDasharray="2 4" />
            <text x={x(tk.t)} y={base + 18} textAnchor="middle" fontSize={11} fill={colors.textFaint}>
              {tk.label}
            </text>
          </g>
        ))}

        {/* spikes */}
        {rows.map((r, i) => {
          if (r.total === 0) return null
          const left = Math.max(LEFT, x(r.t))
          const right = Math.min(W - RIGHT, x(r.t + bucketMs))
          const w = Math.max(2, right - left - 1)
          const h = Math.max(3, base - y(r.total))
          const errH = r.errors ? Math.max(ERROR_MIN, (r.errors / r.total) * h) : 0
          const okScale = r.total - r.errors > 0 ? (h - errH) / (r.total - r.errors) : 0
          let top = base
          const slot = { from: r.t, to: r.t + bucketMs }
          const dim = hover !== null && hover !== i
          return (
            <g key={r.t} opacity={dim ? 0.55 : 1} onPointerEnter={() => setHover(i)}>
              {/* a wide invisible target so thin spikes are easy to hover */}
              <rect x={left} y={TOP} width={Math.max(w, 6)} height={PLOT} fill="transparent" />
              {r.segments.map((s) => {
                const sh = s.n * okScale
                top -= sh
                const count = timeline.buckets[i].by_type[s.type] ?? s.n
                const isOpen = selectedGroup?.type === s.type && selectedGroup.from === r.t
                const label = `${count.toLocaleString()} × ${s.type}, ${formatDateTime(r.t)}. Show these events`
                return (
                  <rect
                    key={s.type}
                    data-mark
                    role="button"
                    tabIndex={-1}
                    aria-label={label}
                    x={left}
                    y={top}
                    width={w}
                    height={Math.max(sh, 0.5)}
                    fill={typeColor(s.type)}
                    stroke={isOpen ? colors.textStrong : 'none'}
                    strokeWidth={isOpen ? 1.5 : 0}
                    style={{ cursor: 'pointer' }}
                    onClick={() => onGroup({ type: s.type, ...slot, count })}
                  />
                )
              })}
              {r.errors > 0 && (
                <g
                  data-mark
                  role="button"
                  tabIndex={0}
                  aria-label={`${r.errors} errors, ${formatDateTime(r.t)}. Show them`}
                  style={{ cursor: 'pointer' }}
                  onClick={() => onErrors({ ...slot, count: r.errors })}
                  onKeyDown={(k) => {
                    if (k.key === 'Enter' || k.key === ' ') {
                      k.preventDefault()
                      onErrors({ ...slot, count: r.errors })
                    }
                  }}
                >
                  <rect x={left} y={top - errH} width={w} height={errH} fill={colors.danger} />
                  <line
                    x1={left + w / 2} x2={left + w / 2} y1={top - errH} y2={top - errH - 10}
                    stroke={colors.danger} strokeWidth={2} strokeLinecap="round"
                  />
                  <text x={left + w / 2} y={top - errH - 14} textAnchor="middle" fontSize={10.5} fontWeight={600} fill={colors.danger}>
                    {r.errors}
                  </text>
                  {/* marks the slot on the time axis too */}
                  <path d={`M ${left + w / 2 - 4} ${base + 6} L ${left + w / 2 + 4} ${base + 6} L ${left + w / 2} ${base + 1} Z`} fill={colors.danger} />
                </g>
              )}
            </g>
          )
        })}

        {drag && Math.abs(drag.b - drag.a) > 2 && (
          <rect
            x={Math.max(LEFT, Math.min(drag.a, drag.b))} y={TOP}
            width={Math.abs(drag.b - drag.a)} height={PLOT}
            fill="rgba(99,102,241,0.15)" stroke={colors.info}
          />
        )}
      </svg>

      {hovered && !drag && (
        <div
          role="tooltip"
          className="pointer-events-none absolute text-xs px-3 py-2"
          style={{
            left: tipRight ? `calc(${tipAt}% + 14px)` : undefined,
            right: tipRight ? undefined : `calc(${100 - tipAt}% + 14px)`,
            top: 36,
            minWidth: 190,
            background: colors.surfaceAlt,
            border: `1px solid ${colors.borderStrong}`,
            borderRadius: radii.md,
            color: colors.text,
            boxShadow: '0 8px 24px rgba(0,0,0,0.35)',
          }}
        >
          <div className="font-semibold mb-1" style={{ color: colors.textStrong }}>
            {formatDateTime(hovered.t)} → {formatClock(hovered.t + bucketMs)}
          </div>
          <div className="mb-1" style={{ color: colors.textMuted }}>
            {hovered.total.toLocaleString()} events
            {hovered.errors > 0 && <span style={{ color: colors.danger }}> · {hovered.errors} errors</span>}
          </div>
          {hovered.errorTypes.map((s) => (
            <div key={`e${s.type}`} className="flex items-center justify-between gap-3 font-mono">
              <span className="flex items-center gap-1.5" style={{ color: colors.danger }}>
                <span className="rounded-full" style={{ width: 7, height: 7, background: colors.danger }} />
                {s.type}
              </span>
              <span style={{ color: colors.danger }}>{s.n.toLocaleString()}</span>
            </div>
          ))}
          {[...hovered.segments].sort((a, z) => z.n - a.n).slice(0, 8).map((s) => (
            <div key={s.type} className="flex items-center justify-between gap-3 font-mono">
              <span className="flex items-center gap-1.5">
                <span className="rounded-full" style={{ width: 7, height: 7, background: typeColor(s.type) }} />
                {s.type}
              </span>
              <span style={{ color: colors.textMuted }}>{s.n.toLocaleString()}</span>
            </div>
          ))}
          {hovered.segments.length > 8 && (
            <div style={{ color: colors.textFaint }}>+ {hovered.segments.length - 8} more types</div>
          )}
        </div>
      )}
    </div>
  )
}

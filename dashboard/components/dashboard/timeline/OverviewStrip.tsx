'use client'

import { useRef, useState, type PointerEvent } from 'react'
import type { AgentTimeline } from '@/lib/api'
import { colors } from '@/lib/design-tokens'
import { DAY, MIN_SPAN, clampRange, formatDay, type TimeRange } from '@/lib/timeline'

const W = 900
const LEFT = 150
const RIGHT = 16
const H = 64
const TOP = 6
const BASE = 46

interface Props {
  overview: AgentTimeline | null
  domain: TimeRange
  range: TimeRange
  /** Called once the drag ends, with the new window. */
  onChange: (range: TimeRange) => void
}

type Drag =
  | { kind: 'from' | 'to'; start: TimeRange }
  | { kind: 'move'; start: TimeRange; offset: number }
  | { kind: 'new'; start: TimeRange; anchor: number }

/**
 * The whole period (usually the last 30 days) with the selected window on it.
 * Drag the window to move it, drag its edges to resize it, or drag anywhere
 * else to draw a new one.
 */
export function OverviewStrip({ overview, domain, range, onChange }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [live, setLive] = useState<TimeRange | null>(null)
  const view = live ?? range

  const plotW = W - LEFT - RIGHT
  const x = (t: number) => LEFT + ((t - domain.from) / (domain.to - domain.from)) * plotW
  const toTime = (px: number) =>
    domain.from + ((Math.min(W - RIGHT, Math.max(LEFT, px)) - LEFT) / plotW) * (domain.to - domain.from)
  const svgX = (ev: PointerEvent) => {
    const r = svgRef.current!.getBoundingClientRect()
    return ((ev.clientX - r.left) / r.width) * W
  }

  const buckets = (overview?.buckets ?? []).map((b) => ({ t: new Date(b.start).getTime(), n: b.total, e: b.errors }))
  const bucketMs = (overview?.bucket_seconds ?? 21600) * 1000
  const maxN = Math.max(1, ...buckets.map((b) => b.n))
  const maxE = Math.max(1, ...buckets.map((b) => b.e))
  const y = (n: number) => BASE - (n / maxN) * (BASE - TOP - 4)
  let area = `M${LEFT},${BASE}`
  buckets.forEach((b) => {
    area += ` L${x(b.t)},${y(b.n)} L${x(b.t + bucketMs)},${y(b.n)}`
  })
  area += ` L${W - RIGHT},${BASE} Z`

  const labels: number[] = []
  const labelStep = domain.to - domain.from > 60 * DAY ? 30 * DAY : 5 * DAY
  for (let t = domain.from; t <= domain.to; t += labelStep) labels.push(t)

  const x0 = x(view.from)
  const x1 = Math.max(x(view.to), x0 + 4)

  const onDown = (ev: PointerEvent<SVGSVGElement>) => {
    const role = (ev.target as Element).getAttribute('data-role')
    const t = toTime(svgX(ev))
    if (role === 'from' || role === 'to') setDrag({ kind: role, start: range })
    else if (role === 'window') setDrag({ kind: 'move', start: range, offset: t - range.from })
    else setDrag({ kind: 'new', start: range, anchor: t })
    svgRef.current?.setPointerCapture(ev.pointerId)
  }
  const onMove = (ev: PointerEvent<SVGSVGElement>) => {
    if (!drag) return
    const t = toTime(svgX(ev))
    const s = drag.start
    let next: TimeRange
    if (drag.kind === 'from') next = { from: Math.min(t, s.to - MIN_SPAN), to: s.to }
    else if (drag.kind === 'to') next = { from: s.from, to: Math.max(t, s.from + MIN_SPAN) }
    else if (drag.kind === 'move') {
      const span = s.to - s.from
      const from = Math.max(domain.from, Math.min(domain.to - span, t - drag.offset))
      next = { from, to: from + span }
    } else if (drag.kind === 'new') next = { from: Math.min(drag.anchor, t), to: Math.max(drag.anchor, t) }
    else return
    setLive(clampRange(next, domain.from, domain.to))
  }
  const onUp = () => {
    if (live) onChange(live)
    setDrag(null)
    setLive(null)
  }

  return (
    <div className="overflow-x-auto">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        style={{ minWidth: 640, display: 'block', touchAction: 'none', userSelect: 'none', cursor: 'crosshair' }}
        role="img"
        aria-label="Overview of the whole period with the selected window"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={() => {
          setDrag(null)
          setLive(null)
        }}
      >
        <text x={LEFT - 10} y={28} textAnchor="end" fontSize={11} fill={colors.textFaint}>
          {Math.round((domain.to - domain.from) / DAY)} days
        </text>
        <path d={area} fill="rgba(154,167,184,0.22)" stroke="rgba(154,167,184,0.45)" strokeWidth={1} />
        {buckets.filter((b) => b.e > 0).map((b) => (
          <line
            key={b.t}
            x1={x(b.t + bucketMs / 2)} x2={x(b.t + bucketMs / 2)}
            y1={BASE} y2={BASE - 4 - (b.e / maxE) * (BASE - TOP - 8)}
            stroke={colors.danger} strokeWidth={2}
          />
        ))}
        {labels.map((t) => (
          <text key={t} x={x(t)} y={H - 3} textAnchor="middle" fontSize={10.5} fill={colors.textFaint}>
            {formatDay(t)}
          </text>
        ))}
        <rect x={LEFT} y={TOP - 2} width={Math.max(0, x0 - LEFT)} height={BASE - TOP + 2} fill="rgba(11,15,22,0.6)" />
        <rect x={x1} y={TOP - 2} width={Math.max(0, W - RIGHT - x1)} height={BASE - TOP + 2} fill="rgba(11,15,22,0.6)" />
        <rect
          data-role="window"
          x={x0} y={TOP - 2} width={x1 - x0} height={BASE - TOP + 2} rx={3}
          fill="rgba(99,102,241,0.14)" stroke={colors.info} strokeWidth={1.5}
          style={{ cursor: drag?.kind === 'move' ? 'grabbing' : 'grab' }}
        />
        <rect data-role="from" x={x0 - 4} y={TOP + 8} width={8} height={BASE - TOP - 16} rx={2} fill={colors.info} style={{ cursor: 'ew-resize' }} />
        <rect data-role="to" x={x1 - 4} y={TOP + 8} width={8} height={BASE - TOP - 16} rx={2} fill={colors.info} style={{ cursor: 'ew-resize' }} />
      </svg>
    </div>
  )
}

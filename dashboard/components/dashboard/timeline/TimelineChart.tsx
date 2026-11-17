'use client'

import { useRef, useState, type PointerEvent } from 'react'
import type { AgentTimeline, TimelineEvent } from '@/lib/api'
import { colors } from '@/lib/design-tokens'
import {
  axisTicks,
  bucketLabel,
  formatDateTime,
  typeColor,
  type TimeRange,
} from '@/lib/timeline'

const W = 900
const LEFT = 150
const RIGHT = 16
const TOP = 14
const STRIP = 70
const GAP = 24
const LANE = 28

interface Props {
  timeline: AgentTimeline
  range: TimeRange
  lanes: string[]
  selected: TimelineEvent | null
  onSelect: (event: TimelineEvent) => void
  onZoom: (range: TimeRange) => void
}

/**
 * The main graph: an activity strip (events per bucket, red spikes for errors)
 * over one lane per event type. Every event is a dot when the window holds few
 * enough; otherwise ordinary events are grouped per bucket and errors are still
 * drawn one by one. Drag across the graph to zoom in.
 */
export function TimelineChart({ timeline, range, lanes, selected, onSelect, onZoom }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null)

  const plotW = W - LEFT - RIGHT
  const x = (t: number) => LEFT + ((t - range.from) / (range.to - range.from)) * plotW
  const laneTop = TOP + STRIP + GAP
  const plotBottom = laneTop + Math.max(lanes.length, 1) * LANE
  const height = plotBottom + 30
  const laneY = (type: string) => laneTop + lanes.indexOf(type) * LANE + LANE / 2
  const bucketMs = timeline.bucket_seconds * 1000
  const grouped = timeline.events === null
  const shown = new Set(lanes)

  const stripRows = timeline.buckets.map((b) => {
    const total = Object.entries(b.by_type).reduce((n, [type, c]) => n + (shown.has(type) ? c : 0), 0)
    return { t: new Date(b.start).getTime(), total, errors: b.errors }
  })
  const strip = {
    rows: stripRows,
    max: Math.max(1, ...stripRows.map((r) => r.total)),
    maxErr: Math.max(1, ...stripRows.map((r) => r.errors)),
  }

  const yStrip = (n: number) => TOP + STRIP - (n / strip.max) * (STRIP - 10)
  const mid = (t: number) => Math.min(W - RIGHT, Math.max(LEFT, x(t + bucketMs / 2)))

  const points = (grouped ? [] : timeline.events!).filter((e) => shown.has(e.event))
  const errors = (grouped ? timeline.errors : points.filter((e) => e.is_error)).filter((e) => shown.has(e.event))
  const ordinary = points.filter((e) => !e.is_error)
  const chatLine = !grouped && selected?.session_id
    ? points.filter((e) => e.session_id === selected.session_id)
    : []

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

  const mark = (e: TimelineEvent, error: boolean) => {
    const cx = x(new Date(e.timestamp).getTime())
    const cy = laneY(e.event)
    const isSel = selected?.event_id === e.event_id
    const inChat = !!selected?.session_id && e.session_id === selected.session_id
    const label = `${e.event} at ${formatDateTime(new Date(e.timestamp).getTime())}${e.session_id ? ` in ${e.session_id}` : ''}`
    return (
      <g
        key={e.event_id}
        data-mark
        role="button"
        tabIndex={error ? 0 : -1}
        aria-label={label}
        style={{ cursor: 'pointer' }}
        onClick={() => onSelect(e)}
        onKeyDown={(k) => {
          if (k.key === 'Enter' || k.key === ' ') {
            k.preventDefault()
            onSelect(e)
          }
        }}
      >
        <title>{label}</title>
        {error && <circle cx={cx} cy={cy} r={9} fill="rgba(248,113,113,0.18)" />}
        {isSel && <circle cx={cx} cy={cy} r={11} fill="none" stroke={colors.textStrong} strokeWidth={1.5} />}
        <circle
          cx={cx}
          cy={cy}
          r={error ? 5.5 : inChat ? 4.5 : 3.2}
          fill={error ? colors.danger : typeColor(e.event)}
        />
      </g>
    )
  }

  return (
    <div className="overflow-x-auto">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${height}`}
        width="100%"
        style={{ minWidth: 640, display: 'block', touchAction: 'none', userSelect: 'none' }}
        role="img"
        aria-label="Event timeline: time across, one lane per event type"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={() => setDrag(null)}
      >
        {/* grid and time axis */}
        {axisTicks(range).map((tk) => (
          <g key={tk.t}>
            <line x1={x(tk.t)} x2={x(tk.t)} y1={TOP} y2={plotBottom} stroke={colors.surfaceHover} />
            <text x={x(tk.t)} y={plotBottom + 18} textAnchor="middle" fontSize={11} fill={colors.textFaint}>
              {tk.label}
            </text>
          </g>
        ))}

        {/* activity strip */}
        <text x={LEFT - 34} y={TOP + STRIP / 2 + 4} textAnchor="end" fontSize={11} fill={colors.textFaint}>
          events / {bucketLabel(timeline.bucket_seconds)}
        </text>
        <text x={LEFT - 10} y={yStrip(strip.max) + 4} textAnchor="end" fontSize={11} fill={colors.textFaint}>
          {strip.max}
        </text>
        <text x={LEFT - 10} y={TOP + STRIP} textAnchor="end" fontSize={11} fill={colors.textFaint}>0</text>
        <line x1={LEFT} x2={W - RIGHT} y1={TOP + STRIP} y2={TOP + STRIP} stroke={colors.border} />
        {strip.rows.map((r) => (
          <rect
            key={r.t}
            x={Math.max(LEFT, x(r.t))}
            y={yStrip(r.total)}
            width={Math.max(1, Math.min(W - RIGHT, x(r.t + bucketMs)) - Math.max(LEFT, x(r.t)) - 1)}
            height={TOP + STRIP - yStrip(r.total)}
            fill="rgba(99,102,241,0.35)"
          />
        ))}
        {strip.rows.filter((r) => r.errors > 0).map((r) => {
          const h = 8 + (r.errors / strip.maxErr) * (STRIP - 14)
          return (
            <g key={`e${r.t}`}>
              <line
                x1={mid(r.t)} x2={mid(r.t)} y1={TOP + STRIP} y2={TOP + STRIP - h}
                stroke={colors.danger} strokeWidth={2.5} strokeLinecap="round"
              />
              {r.errors > 1 && (
                <text x={mid(r.t)} y={TOP + STRIP - h - 3} textAnchor="middle" fontSize={10} fill={colors.danger}>
                  {r.errors}
                </text>
              )}
            </g>
          )
        })}

        {/* lanes */}
        {lanes.map((type) => (
          <g key={type}>
            <line x1={LEFT} x2={W - RIGHT} y1={laneY(type)} y2={laneY(type)} stroke={colors.surfaceHover} />
            <text
              x={LEFT - 10} y={laneY(type) + 4} textAnchor="end" fontSize={11.5}
              fill={typeColor(type) === colors.danger ? colors.danger : colors.textMuted}
              fontFamily="var(--font-mono, monospace)"
            >
              {type.length > 20 ? `${type.slice(0, 19)}…` : type}
            </text>
          </g>
        ))}

        {/* a faint red guide through every error */}
        {errors.map((e) => {
          const ex = x(new Date(e.timestamp).getTime())
          return (
            <line
              key={`g${e.event_id}`} x1={ex} x2={ex} y1={TOP + STRIP} y2={plotBottom}
              stroke="rgba(248,113,113,0.22)" strokeDasharray="3 3"
            />
          )
        })}

        {/* the selected event's chat, connected in order */}
        {chatLine.length > 1 && (
          <polyline
            points={chatLine.map((e) => `${x(new Date(e.timestamp).getTime())},${laneY(e.event)}`).join(' ')}
            fill="none" stroke="rgba(232,237,245,0.55)" strokeWidth={1.2}
          />
        )}

        {/* grouped: bubbles per bucket and type (click to zoom in) */}
        {grouped &&
          timeline.buckets.flatMap((b) =>
            Object.entries(b.by_type)
              .filter(([type]) => shown.has(type) && typeColor(type) !== colors.danger)
              .map(([type, n]) => {
                const t = new Date(b.start).getTime()
                const label = `${n} × ${type}, ${formatDateTime(t)}. Zoom in`
                return (
                  <g
                    key={`${b.start}|${type}`}
                    data-mark
                    role="button"
                    tabIndex={-1}
                    aria-label={label}
                    style={{ cursor: 'zoom-in' }}
                    onClick={() => onZoom({ from: t, to: t + bucketMs })}
                  >
                    <title>{label}</title>
                    <circle cx={mid(t)} cy={laneY(type)} r={Math.min(10, 2 + Math.sqrt(n) * 1.3)} fill={typeColor(type)} opacity={0.75} />
                  </g>
                )
              }),
          )}

        {ordinary.map((e) => mark(e, false))}
        {errors.map((e) => mark(e, true))}

        {drag && Math.abs(drag.b - drag.a) > 2 && (
          <rect
            x={Math.max(LEFT, Math.min(drag.a, drag.b))} y={TOP}
            width={Math.abs(drag.b - drag.a)} height={plotBottom - TOP}
            fill="rgba(99,102,241,0.15)" stroke={colors.info}
          />
        )}
      </svg>
    </div>
  )
}

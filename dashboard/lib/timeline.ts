/**
 * Pure helpers for the event timeline graph (components/dashboard/timeline).
 * Time is in epoch milliseconds throughout; labels use the viewer's local time.
 */

import { colors } from './design-tokens'
import { isErrorEvent } from './events'

export const MIN = 60_000
export const HOUR = 60 * MIN
export const DAY = 24 * HOUR

/** The narrowest window the graph zooms to. */
export const MIN_SPAN = 5 * MIN
/** The server's limit on one window (services/reports.py MAX_RANGE_DAYS). */
export const MAX_SPAN = 366 * DAY

/** The current time; one place to read the clock, outside render. */
export function currentTime(): number {
  return Date.now()
}

export type PresetId = '1h' | '24h' | '7d' | '30d'

export const PRESETS: { id: PresetId; label: string; span: number }[] = [
  { id: '1h', label: '1h', span: HOUR },
  { id: '24h', label: '24h', span: DAY },
  { id: '7d', label: '7 days', span: 7 * DAY },
  { id: '30d', label: '30 days', span: 30 * DAY },
]

export interface TimeRange {
  from: number
  to: number
}

export function presetRange(id: PresetId, now: number): TimeRange {
  const span = PRESETS.find((p) => p.id === id)!.span
  return { from: now - span, to: now }
}

/** The preset a range matches (ends now, same span), if any. */
export function activePreset(range: TimeRange, now: number): PresetId | null {
  const tolerance = MIN
  if (Math.abs(range.to - now) > tolerance) return null
  const hit = PRESETS.find((p) => Math.abs(range.to - range.from - p.span) <= tolerance)
  return hit ? hit.id : null
}

/** Keep a range inside [lo, hi], at least MIN_SPAN and at most MAX_SPAN wide. */
export function clampRange(range: TimeRange, lo: number, hi: number): TimeRange {
  let from = Math.min(range.from, range.to)
  let to = Math.max(range.from, range.to)
  if (to - from > MAX_SPAN) from = to - MAX_SPAN
  if (to - from < MIN_SPAN) {
    const mid = (from + to) / 2
    from = mid - MIN_SPAN / 2
    to = mid + MIN_SPAN / 2
  }
  if (from < lo) {
    to += lo - from
    from = lo
  }
  if (to > hi) {
    from -= to - hi
    to = hi
  }
  return { from: Math.max(lo, from), to }
}

const TICK_STEPS = [
  MIN, 5 * MIN, 15 * MIN, 30 * MIN, HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY,
]
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const pad = (n: number) => String(n).padStart(2, '0')

export const formatClock = (t: number) => {
  const d = new Date(t)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}
export const formatClockSeconds = (t: number) => `${formatClock(t)}:${pad(new Date(t).getSeconds())}`
export const formatDay = (t: number) => {
  const d = new Date(t)
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`
}
export const formatDateTime = (t: number) => `${formatDay(t)}, ${formatClockSeconds(t)}`

/** Value for <input type="datetime-local">, in local time. */
export function toLocalInput(t: number): string {
  const d = new Date(t)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${formatClock(t)}`
}

/**
 * Axis ticks for a window: at most ~9, on round local times, labelled with the
 * clock for short windows and the date for long ones.
 */
export function axisTicks(range: TimeRange): { t: number; label: string }[] {
  const span = range.to - range.from
  const step = TICK_STEPS.find((s) => span / s <= 9) ?? TICK_STEPS[TICK_STEPS.length - 1]
  const out: { t: number; label: string }[] = []
  // Align to local midnight so day and hour ticks land on round local times.
  const startOfDay = new Date(range.from)
  startOfDay.setHours(0, 0, 0, 0)
  let t = startOfDay.getTime()
  if (step < DAY) {
    t += Math.ceil((range.from - t) / step) * step
  } else {
    while (t < range.from) t += DAY
  }
  for (; t <= range.to && out.length < 40; t += step) {
    const d = new Date(t)
    const midnight = d.getHours() === 0 && d.getMinutes() === 0
    let label: string
    if (step >= DAY) label = formatDay(t)
    else if (span > DAY || midnight) label = midnight ? formatDay(t) : `${formatDay(t)} ${formatClock(t)}`
    else label = formatClock(t)
    out.push({ t, label })
  }
  return out
}

/** "1 min", "15 min", "3 h", "day", "week": the size of one bucket. */
export function bucketLabel(seconds: number): string {
  if (seconds >= 7 * 86400) return 'week'
  if (seconds >= 86400) return 'day'
  if (seconds >= 3600) return `${seconds / 3600} h`
  return `${seconds / 60} min`
}

// Distinct, non-red hues for ordinary event types; red is reserved for errors.
const KNOWN_COLORS: Record<string, string> = {
  user_message: '#94a3b8',
  llm_start: '#818cf8',
  llm_end: '#6366f1',
  tool_call: '#22d3ee',
  tool_result: '#22c55e',
  assistant_response: '#c084fc',
  agent_start: '#2dd4bf',
  agent_end: '#a3e635',
}
const PALETTE = ['#38bdf8', '#a78bfa', '#34d399', '#fbbf24', '#f472b6', '#60a5fa', '#4ade80', '#e879f9', '#facc15', '#2dd4bf']

function hash(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return Math.abs(h)
}

/** One stable colour per event type; error types are always red. */
export function typeColor(type: string): string {
  if (isErrorEvent(type)) return colors.danger
  return KNOWN_COLORS[type] ?? PALETTE[hash(type) % PALETTE.length]
}

/** Lane order: ordinary types by volume, then error types last (nearest the reader's eye at the bottom). */
export function laneOrder(types: { event: string; count: number }[]): string[] {
  const ordinary = types.filter((t) => !isErrorEvent(t.event))
  const errors = types.filter((t) => isErrorEvent(t.event))
  const byCount = (a: { event: string; count: number }, b: { event: string; count: number }) =>
    b.count - a.count || a.event.localeCompare(b.event)
  return [...ordinary.sort(byCount), ...errors.sort(byCount)].map((t) => t.event)
}

/** A grouped dot: every event of one type in one time slot (zoomed-out graph). */
export interface TimelineGroup {
  type: string
  from: number
  to: number
  count: number
}

/** The most events the group list loads (the events API's page limit). */
export const GROUP_LIST_MAX = 1000

/**
 * Query for the events behind a grouped dot: that type, start of the slot
 * included, end excluded (the events API's `after` / `before` are exclusive).
 */
export function groupQuery(group: TimelineGroup, limit: number): Record<string, string> {
  return {
    event_type: group.type,
    after: new Date(group.from - 1).toISOString(),
    before: new Date(group.to).toISOString(),
    limit: String(Math.min(limit, GROUP_LIST_MAX)),
  }
}

/** Whether zooming into the group's slot would show a narrower window than now. */
export function canZoomInto(range: TimeRange): boolean {
  return range.to - range.from > MIN_SPAN + 1000
}

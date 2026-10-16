/**
 * Pure, presentation-agnostic helpers for event data.
 *
 * Extracted verbatim (behaviour-wise) from the old agent-detail monolith so the
 * logic is unit-testable and shared instead of duplicated per screen.
 */

import { ACTIVE_WINDOW_MS, colors, type StatusTone } from './design-tokens'
import type { AgentEvent } from './api'

/** Event types containing these substrings are treated as failures. */
const ERROR_HINTS = ['error', 'fail']

export function isErrorEvent(eventType: string): boolean {
  const t = (eventType || '').toLowerCase()
  return ERROR_HINTS.some((h) => t.includes(h))
}

/**
 * Which tool or API an event is about, read from its payload: `data.tool`
 * (or `tool_name`) for tools, `data.api` — or method + endpoint/url on
 * `api_*` / `http_*` events — for APIs. Mirrors core/services/actions.py.
 */
export function eventActionName(event: Pick<AgentEvent, 'event' | 'data'>): string | null {
  const data = (event.data ?? {}) as Record<string, unknown>
  const text = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : null)

  const named = text(data.tool) ?? text(data.tool_name) ?? text(data.api)
  if (named) return named

  const type = (event.event || '').toLowerCase()
  if (type.startsWith('api') || type.startsWith('http')) {
    const target = text(data.endpoint) ?? text(data.url)
    const parts = [text(data.method), target].filter(Boolean)
    if (parts.length) return parts.join(' ')
  }
  return null
}

/** `tool_call · lookup_order` when the event names its tool/API, else the type. */
export function eventLabel(event: Pick<AgentEvent, 'event' | 'data'>): string {
  const name = eventActionName(event)
  return name ? `${event.event} · ${name}` : event.event
}

/** Stable colour per event type (errors red, lifecycle green, else neutral). */
export function eventColor(type: string): string {
  const t = (type || '').toLowerCase()
  if (isErrorEvent(t)) return colors.danger
  if (t.includes('start') || t.includes('create') || t.includes('complete')) {
    return colors.success
  }
  if (t.includes('tool') || t.includes('call')) return colors.info
  if (t.includes('state')) return colors.warning
  return colors.textMuted
}

/**
 * Group events into ordered session buckets, preserving input order.
 * Events without a session_id collapse into a single "no session" bucket
 * (keyed by null) rather than being dropped.
 */
export function groupBySession(
  events: AgentEvent[],
): { sessionId: string | null; events: AgentEvent[] }[] {
  const order: (string | null)[] = []
  const buckets = new Map<string | null, AgentEvent[]>()

  for (const ev of events) {
    const key = ev.session_id ?? null
    if (!buckets.has(key)) {
      buckets.set(key, [])
      order.push(key)
    }
    buckets.get(key)!.push(ev)
  }

  return order.map((sessionId) => ({
    sessionId,
    events: buckets.get(sessionId)!,
  }))
}

/** Top-n entries of a distribution map, highest count first. */
export function topN(
  dist: Record<string, number>,
  n: number,
): [string, number][] {
  return Object.entries(dist)
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
}

/**
 * The search endpoint returns either `{results: [...]}` or a bare array
 * depending on the path taken. Normalise once, here, instead of at each
 * call site (this inconsistency was duplicated in two screens).
 */
export function normalizeSearchResults(res: unknown): AgentEvent[] {
  if (Array.isArray(res)) return res as AgentEvent[]
  if (res && typeof res === 'object' && 'results' in res) {
    const inner = (res as { results?: unknown }).results
    return Array.isArray(inner) ? (inner as AgentEvent[]) : []
  }
  return []
}

/** Health status for an agent, from last-seen recency and error signal. */
export function deriveStatus(input: {
  lastSeen?: string | null
  hasErrors?: boolean
  hasDrift?: boolean
}): StatusTone {
  if (input.hasErrors) return 'error'
  if (input.hasDrift) return 'drift'
  if (!input.lastSeen) return 'idle'

  const ts = new Date(input.lastSeen).getTime()
  if (Number.isNaN(ts)) return 'idle'
  return Date.now() - ts < ACTIVE_WINDOW_MS ? 'healthy' : 'idle'
}

/**
 * Who is answerable for an event: the agent, the credential that wrote it
 * (sealed by the server as metadata.written_by, core/services/writer.py), the
 * model and the agent version; and where a causal chain first went wrong.
 */

import type { AgentEvent } from './api'
import { isErrorEvent } from './events'

type Meta = Record<string, unknown> | null | undefined

export interface WrittenBy {
  kind: 'api_key' | 'dashboard' | 'dev_key' | 'unknown'
  key_id?: string
  key_prefix?: string
  user_id?: string
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
}

export function writtenBy(metadata: Meta): WrittenBy | null {
  const w = asRecord(asRecord(metadata)?.written_by)
  if (!w || typeof w.kind !== 'string') return null
  return w as unknown as WrittenBy
}

/** "API key zizkadb_live_ab12…", "Dashboard user", "Development key", or "Not recorded". */
export function writerLabel(metadata: Meta): string {
  const w = writtenBy(metadata)
  if (!w) return 'Not recorded (written before writers were recorded)'
  if (w.kind === 'api_key') return w.key_prefix ? `API key ${w.key_prefix}…` : `API key ${w.key_id ?? ''}`.trim()
  if (w.kind === 'dashboard') return 'Dashboard user (not the agent’s own key)'
  if (w.kind === 'dev_key') return 'Development key'
  return 'Unknown credential'
}

/** The model an event names, from data.model or data.token_usage.model. */
export function modelOf(event: Pick<AgentEvent, 'data'>): string | null {
  const data = asRecord(event.data) ?? {}
  if (typeof data.model === 'string' && data.model) return data.model
  const usage = asRecord(data.token_usage)
  return typeof usage?.model === 'string' && usage.model ? usage.model : null
}

/** The agent version an event was recorded with (metadata.agent_version, set by the SDKs). */
export function agentVersionOf(metadata: Meta): string | null {
  const v = asRecord(metadata)?.agent_version
  return typeof v === 'string' && v ? v : null
}

/** An event that failed: its type says so, or its data carries an error. */
export function eventFailed(event: Pick<AgentEvent, 'event' | 'data'>): boolean {
  return isErrorEvent(event.event) || asRecord(event.data)?.error != null
}

/**
 * Where a chain (root first) first went wrong: the earliest failed event, or
 * null when nothing in it failed.
 */
export function firstFailure<T extends Pick<AgentEvent, 'event' | 'data'>>(chain: T[]): T | null {
  return chain.find(eventFailed) ?? null
}

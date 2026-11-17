/**
 * Agent-to-agent handoffs that keep the causal chain across processes,
 * frameworks and LLMs. Same token format as the Python SDK (`zdbh1.` +
 * base64url JSON), so a Python agent can hand off to a TypeScript one and back.
 *
 * The token carries ids only (the message event, its session, the sender and
 * recipient), never content. It is not a secret: the server still checks that
 * every linked event belongs to the caller's account.
 */

export const HANDOFF_TOKEN_PREFIX = 'zdbh1.'
export const HANDOFF_HEADER = 'X-ZizkaDB-Handoff'
export const HANDOFF_SENT_EVENT = 'a2a.message'
export const HANDOFF_RECEIVED_EVENT = 'a2a.received'

export interface HandoffToken {
  eventId: string
  sessionId: string | null
  fromAgent: string
  toAgent: string
  messageId?: string | null
  /** Set when the server already recorded the receipt in the recipient's chain. */
  receivedEventId?: string | null
}

export interface Handoff extends HandoffToken {
  token: string
  /** `a2a`: through /v1/a2a/messages (both sides recorded); `event`: the sender's a2a.message event. */
  recordedBy: 'a2a' | 'event'
  /** HTTP headers that carry the token. */
  headers: Record<string, string>
}

function toBase64Url(text: string): string {
  const bytes = new TextEncoder().encode(text)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function fromBase64Url(text: string): string {
  const b64 = text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4)
  const bin = atob(b64)
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0))
  return new TextDecoder().decode(bytes)
}

export function encodeHandoffToken(t: HandoffToken): string {
  const raw: Record<string, unknown> = { v: 1, e: t.eventId, s: t.sessionId, f: t.fromAgent, t: t.toAgent }
  if (t.messageId) raw.m = t.messageId
  if (t.receivedEventId) raw.r = t.receivedEventId
  return HANDOFF_TOKEN_PREFIX + toBase64Url(JSON.stringify(raw))
}

export function decodeHandoffToken(token: string): HandoffToken {
  if (typeof token !== 'string' || !token.startsWith(HANDOFF_TOKEN_PREFIX)) {
    throw new Error("Not a ZizkaDB handoff token (expected 'zdbh1.…')")
  }
  let raw: Record<string, unknown>
  try {
    raw = JSON.parse(fromBase64Url(token.slice(HANDOFF_TOKEN_PREFIX.length)))
  } catch {
    throw new Error('Malformed ZizkaDB handoff token')
  }
  if (!raw || raw.v !== 1 || !raw.e || !raw.f || !raw.t) throw new Error('Malformed ZizkaDB handoff token')
  return {
    eventId: String(raw.e),
    sessionId: (raw.s as string | null) ?? null,
    fromAgent: String(raw.f),
    toAgent: String(raw.t),
    messageId: (raw.m as string | undefined) ?? null,
    receivedEventId: (raw.r as string | undefined) ?? null,
  }
}

type HeaderBag = { get(name: string): string | null } | Record<string, string | string[] | undefined> | null | undefined

/** The handoff token from incoming request headers (Fetch `Headers` or a plain object), if any. */
export function tokenFromHeaders(headers: HeaderBag): string | null {
  if (!headers) return null
  if (typeof (headers as { get?: unknown }).get === 'function') {
    return (headers as { get(name: string): string | null }).get(HANDOFF_HEADER) ?? null
  }
  const wanted = HANDOFF_HEADER.toLowerCase()
  for (const [key, value] of Object.entries(headers as Record<string, string | string[] | undefined>)) {
    if (key.toLowerCase() === wanted && value) return Array.isArray(value) ? value[0] ?? null : value
  }
  return null
}

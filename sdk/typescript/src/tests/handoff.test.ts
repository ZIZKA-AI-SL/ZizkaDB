import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  ZizkaDB,
  CloudOnlyError,
  NotFoundError,
  HANDOFF_HEADER,
  decodeHandoffToken,
  encodeHandoffToken,
  tokenFromHeaders,
} from '../index'

for (const name of ['ZIZKADB_HOST', 'AGENTDB_HOST', 'ZIZKADB_AGENT_VERSION']) vi.stubEnv(name, '')
// No install ping through the mocked fetch.
vi.stubEnv('ZIZKADB_TELEMETRY', 'false')

// A token made by the Python SDK (HandoffToken(e1, s1, planner, billing, m1, r1).encode()).
const PYTHON_TOKEN =
  'zdbh1.eyJ2IjoxLCJlIjoiZTEiLCJzIjoiczEiLCJmIjoicGxhbm5lciIsInQiOiJiaWxsaW5nIiwibSI6Im0xIiwiciI6InIxIn0'

type Call = { path: string; body: Record<string, any> }

/** A fake server: /v1/a2a/messages answers like Cloud, or 404s like a self-hosted server. */
function server({ a2a = true, receipt = true } = {}) {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    const path = new URL(url).pathname
    const body = JSON.parse(String(init.body ?? '{}'))
    calls.push({ path, body })
    const n = calls.length
    const base = { timestamp: '2026-01-01T00:00:00+00:00', sequence_no: n, checksum: 'c', indexed: false }
    if (path === '/v1/a2a/messages') {
      if (!a2a) return { status: 404, statusText: '404', json: async () => ({ detail: 'Not Found' }) }
      return {
        status: 201, statusText: '201',
        json: async () => ({ ...base, event_id: `sent-${n}`, message_id: 'msg-1', session_id: body.session_id,
          ...(receipt ? { received_event_id: `recv-${n}` } : {}) }),
      }
    }
    if (path === '/v1/events/batch') {
      return { status: 200, statusText: '200', json: async () => ({ written: 1, failed: 0, results: [{ index: 0, ok: true, ...base, event_id: `evt-${n}` }] }) }
    }
    return { status: 201, statusText: '201', json: async () => ({ ...base, event_id: `evt-${n}` }) }
  }))
  return calls
}

beforeEach(() => vi.unstubAllGlobals())

describe('handoff token', () => {
  it('round-trips and reads tokens made by the Python SDK', () => {
    const t = { eventId: 'e1', sessionId: 's1', fromAgent: 'planner', toAgent: 'billing', messageId: 'm1', receivedEventId: 'r1' }
    expect(encodeHandoffToken(t)).toBe(PYTHON_TOKEN)
    expect(decodeHandoffToken(PYTHON_TOKEN)).toEqual(t)
    expect(() => decodeHandoffToken('nope')).toThrow()
    expect(() => decodeHandoffToken('zdbh1.%%%')).toThrow()
  })

  it('is read from Fetch headers or a plain object', () => {
    expect(tokenFromHeaders(new Headers({ [HANDOFF_HEADER]: 'zdbh1.x' }))).toBe('zdbh1.x')
    expect(tokenFromHeaders({ 'x-zizkadb-handoff': 'zdbh1.y' })).toBe('zdbh1.y')
    expect(tokenFromHeaders({})).toBeNull()
  })
})

describe('db.handoff / db.continueFrom', () => {
  it('uses /v1/a2a/messages on Cloud and links to what caused it', async () => {
    const calls = server()
    const db = new ZizkaDB({ host: 'http://localhost:8000' })
    const ctx = db.track('planner', 'case-1')
    const h = await ctx.run(async () => {
      await db.log({ agent: 'planner', event: 'user_message', data: {} })
      return db.handoff({ agent: 'planner', to: 'billing', message: 'Refund 4411' })
    })
    const sent = calls[calls.length - 1]
    expect(sent.path).toBe('/v1/a2a/messages')
    expect(sent.body.recipient_agent).toBe('billing')
    expect(sent.body.parent_id).toBe('evt-1')
    expect(sent.body.session_id).toBe('case-1')
    expect(h.recordedBy).toBe('a2a')
    expect(h.headers[HANDOFF_HEADER]).toBe(h.token)
    expect(decodeHandoffToken(h.token).receivedEventId).toBe(h.receivedEventId)
  })

  it('needs ZizkaDB Cloud: a self-hosted server runs one agent', async () => {
    const calls = server({ a2a: false })
    const db = new ZizkaDB({ host: 'http://localhost:8000' })
    await expect(db.handoff({ agent: 'planner', to: 'billing', message: 'hi', sessionId: 'case-1' }))
      .rejects.toBeInstanceOf(CloudOnlyError)
    // Nothing was written for a second agent.
    expect(calls.filter((c) => c.path === '/v1/events')).toHaveLength(0)
  })

  it('a Cloud "not found" stays a NotFoundError with its message', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      status: 404, statusText: '404', json: async () => ({ detail: "Recipient agent 'billing' not found" }),
    })))
    const db = new ZizkaDB({ host: 'http://localhost:8000' })
    const err = await db.handoff({ agent: 'planner', to: 'billing', message: 'hi' }).catch((e) => e)
    expect(err).toBeInstanceOf(NotFoundError)
    expect(err).not.toBeInstanceOf(CloudOnlyError)
    expect(err.message).toBe("Recipient agent 'billing' not found")
  })

  it('the receiver records a2a.received when an older Cloud server did not', async () => {
    const calls = server({ receipt: false })
    const db = new ZizkaDB({ host: 'http://localhost:8000' })
    const h = await db.handoff({ agent: 'planner', to: 'billing', message: 'hi', sessionId: 'case-1' })
    const ctx = await db.continueFrom(h.token, { agent: 'billing' })
    await ctx.run(() => db.log({ agent: 'billing', event: 'tool_call', data: {} }))
    const received = calls[calls.length - 2].body
    expect(received.event).toBe('a2a.received')
    expect(received.agent).toBe('billing')
    expect(received.parent_id).toBe(h.eventId)
    expect(calls[calls.length - 1].body.parent_id).toBe(ctx.receivedEventId)
  })

  it('reuses the receipt the server already recorded', async () => {
    const calls = server()
    const db = new ZizkaDB({ host: 'http://localhost:8000' })
    const h = await db.handoff({ agent: 'planner', to: 'billing', message: 'hi', sessionId: 'case-1' })
    const before = calls.length
    const ctx = await db.continueFrom(h.token, { agent: 'billing' })
    await ctx.run(() => db.log({ agent: 'billing', event: 'tool_call', data: {} }))
    expect(calls.length).toBe(before + 1)
    expect(calls[calls.length - 1].body.parent_id).toBe(h.receivedEventId)
  })
})

describe('agentVersion', () => {
  it('is recorded on log, logBatch, logFork and handoff; an explicit value wins', async () => {
    const calls = server()
    const db = new ZizkaDB({ host: 'http://localhost:8000', agentVersion: 'prompt-v7' })
    await db.log({ agent: 'a', event: 'x', data: {}, metadata: { team: 't' } })
    await db.log({ agent: 'a', event: 'y', data: {}, metadata: { agent_version: 'explicit' } })
    await db.logBatch([{ agent: 'a', event: 'z', data: {} }])
    await db.logFork({ agent: 'a', events: [{ event: 'f', data: {} }] })
    await db.handoff({ agent: 'a', to: 'b', message: 'hi' })
    expect(calls[0].body.metadata).toEqual({ team: 't', agent_version: 'prompt-v7' })
    expect(calls[1].body.metadata).toEqual({ agent_version: 'explicit' })
    expect(calls[2].body.events[0].metadata).toEqual({ agent_version: 'prompt-v7' })
    expect(calls[3].body.metadata).toEqual({ agent_version: 'prompt-v7' })
    expect(calls[4].body.metadata).toEqual({ agent_version: 'prompt-v7' })
  })

  it('leaves metadata alone when not set', async () => {
    const calls = server()
    const db = new ZizkaDB({ host: 'http://localhost:8000' })
    await db.log({ agent: 'a', event: 'x', data: {} })
    expect(calls[0].body.metadata).toBeNull()
  })
})

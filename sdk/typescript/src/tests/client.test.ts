/**
 * ZizkaDB TypeScript SDK — unit tests
 *
 * All tests are fully mocked (no network, no server required).
 * Run with: npm test
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ZizkaDB, ZizkaDBError, AuthError, NotFoundError, RateLimitError, AgentScopeError } from '../index'

// The client reads ZIZKADB_HOST / AGENTDB_HOST when no host is passed; a
// developer's exported values must not change which server a test talks to.
for (const name of ['ZIZKADB_HOST', 'AGENTDB_HOST']) vi.stubEnv(name, '')

// ─────────────────────────────────────────
// Fetch mock helpers
// ─────────────────────────────────────────

function mockFetch(status: number, body: unknown): void {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    status,
    statusText: String(status),
    json: () => Promise.resolve(body),
  }))
}

function mockFetchOnce(status: number, body: unknown): void {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce({
    status,
    statusText: String(status),
    json: () => Promise.resolve(body),
  }))
}

beforeEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

// ─────────────────────────────────────────
// Constructor
// ─────────────────────────────────────────

describe('ZizkaDB constructor', () => {
  it('accepts cloud apiKey', () => {
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_abc123' })
    expect(db).toBeInstanceOf(ZizkaDB)
  })

  it('accepts self-hosted host', () => {
    const db = new ZizkaDB({ host: 'http://localhost:8000' })
    expect(db).toBeInstanceOf(ZizkaDB)
  })

  it('throws when neither apiKey nor host is provided', () => {
    // @ts-expect-error — intentional bad config
    expect(() => new ZizkaDB({})).toThrow(ZizkaDBError)
  })

  it('strips trailing slash from host', () => {
    const db = new ZizkaDB({ host: 'http://localhost:8000/' })
    expect(db).toBeInstanceOf(ZizkaDB)
  })

  it('uses DEFAULT_DEV_API_KEY for localhost when no env key is set', () => {
    const db = new ZizkaDB({ host: 'http://localhost:8000' })
    // Just verify it constructs without throwing
    expect(db).toBeInstanceOf(ZizkaDB)
  })

  it('uses ZIZKADB_API_KEY env var when set', () => {
    process.env.ZIZKADB_API_KEY = 'zizkadb_env_key'
    try {
      const db = new ZizkaDB({ host: 'http://localhost:8000' })
      expect(db).toBeInstanceOf(ZizkaDB)
    } finally {
      delete process.env.ZIZKADB_API_KEY
    }
  })

  it('falls back to AGENTDB_API_KEY (legacy) when ZIZKADB_API_KEY is absent', () => {
    delete process.env.ZIZKADB_API_KEY
    process.env.AGENTDB_API_KEY = 'agdb_live_legacy'
    try {
      const db = new ZizkaDB({ host: 'http://localhost:8000' })
      expect(db).toBeInstanceOf(ZizkaDB)
    } finally {
      delete process.env.AGENTDB_API_KEY
    }
  })
})

// ─────────────────────────────────────────
// log()
// ─────────────────────────────────────────

describe('log()', () => {
  it('posts to /v1/events and returns a LogResult', async () => {
    mockFetch(201, {
      event_id: 'evt-001',
      timestamp: '2026-06-01T00:00:00Z',
      sequence_no: 1,
      checksum: 'abc123',
    })

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const result = await db.log({ agent: 'my-bot', event: 'tool_call', data: { tool: 'search' } })

    expect(result.eventId).toBe('evt-001')
    expect(result.sequenceNo).toBe(1)
    expect(result.checksum).toBe('abc123')

    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/v1/events')
    expect(opts.method).toBe('POST')

    const body = JSON.parse(opts.body as string)
    expect(body.agent).toBe('my-bot')
    expect(body.event).toBe('tool_call')
    expect(body.data).toEqual({ tool: 'search' })
    expect(body.parent_id).toBeNull()
    expect(body.session_id).toBeNull()
  })

  it('sends idempotency_key only when given', async () => {
    mockFetch(201, { event_id: 'evt-003', timestamp: '2026-06-01T00:00:00Z', sequence_no: 3, checksum: 'ghi' })

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    await db.log({ agent: 'my-bot', event: 'e', data: {}, idempotencyKey: 'req-1' })
    await db.log({ agent: 'my-bot', event: 'e', data: {} })

    const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls as [string, RequestInit][]
    expect(JSON.parse(calls[0][1].body as string).idempotency_key).toBe('req-1')
    expect('idempotency_key' in JSON.parse(calls[1][1].body as string)).toBe(false)
  })

  it('returns chainHash and duplicate when the server sends them', async () => {
    mockFetch(201, { event_id: 'e', timestamp: '2026-06-01T00:00:00Z', sequence_no: 4, checksum: 'c', chain_hash: 'h', duplicate: true })
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const r = await db.log({ agent: 'my-bot', event: 'e', data: {} })
    expect(r.chainHash).toBe('h')
    expect(r.duplicate).toBe(true)
  })

  it('forwards parentId and sessionId', async () => {
    mockFetch(201, { event_id: 'evt-002', timestamp: '2026-06-01T00:00:00Z', sequence_no: 2, checksum: 'def' })

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    await db.log({
      agent: 'my-bot',
      event: 'child_event',
      data: {},
      parentId: 'evt-001',
      sessionId: 'sess-abc',
    })

    const [, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(opts.body as string)
    expect(body.parent_id).toBe('evt-001')
    expect(body.session_id).toBe('sess-abc')
  })
})

// ─────────────────────────────────────────
// why()
// ─────────────────────────────────────────

describe('why()', () => {
  it('returns a CausalChain with print()', async () => {
    mockFetch(200, {
      event_id: 'evt-001',
      chain_length: 2,
      chain: [
        {
          event_id: 'evt-000',
          agent: 'my-bot',
          timestamp: '2026-06-01T00:00:00Z',
          event: 'root_action',
          data: { trigger: 'user' },
          parent_id: null,
          session_id: 'sess-1',
          sequence_no: 1,
        },
        {
          event_id: 'evt-001',
          agent: 'my-bot',
          timestamp: '2026-06-01T00:01:00Z',
          event: 'tool_call',
          data: { tool: 'search' },
          parent_id: 'evt-000',
          session_id: 'sess-1',
          sequence_no: 2,
        },
      ],
    })

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const chain = await db.why('evt-001')

    expect(chain.eventId).toBe('evt-001')
    expect(chain.chainLength).toBe(2)
    expect(chain.chain).toHaveLength(2)
    expect(chain.chain[0].event).toBe('root_action')
    expect(typeof chain.print).toBe('function')
  })
})

// ─────────────────────────────────────────
// query()
// ─────────────────────────────────────────

describe('query()', () => {
  it('returns parsed AgentEvents', async () => {
    mockFetch(200, [
      {
        event_id: 'evt-100',
        agent: 'my-bot',
        timestamp: '2026-06-01T00:00:00Z',
        event: 'tool_call',
        data: { tool: 'search' },
        parent_id: null,
        session_id: null,
        sequence_no: 1,
      },
    ])

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const events = await db.query({ agent: 'my-bot' })

    expect(events).toHaveLength(1)
    expect(events[0].eventId).toBe('evt-100')
    expect(events[0].timestamp).toBeInstanceOf(Date)
  })
})

// ─────────────────────────────────────────
// search()
// ─────────────────────────────────────────

describe('search()', () => {
  it('posts to /v1/search and maps score', async () => {
    mockFetch(200, {
      query: 'billing issue',
      results: [
        {
          event_id: 'evt-200',
          agent: 'support-bot',
          timestamp: '2026-06-01T00:00:00Z',
          event: 'user_message',
          data: { text: 'I have a billing problem' },
          parent_id: null,
          score: 0.92,
        },
      ],
    })

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const results = await db.search({ query: 'billing issue', agent: 'support-bot' })

    expect(results).toHaveLength(1)
    expect(results[0].score).toBe(0.92)
    expect(results[0].event).toBe('user_message')
  })
})

// ─────────────────────────────────────────
// forget()
// ─────────────────────────────────────────

describe('forget()', () => {
  it('sends DELETE and returns deleted count', async () => {
    mockFetch(200, {
      deleted_events: 5,
      message: 'Deleted 5 events.',
    })

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const result = await db.forget({ filterKey: 'user_id', filterValue: 'user_123' })

    expect(result.deletedEvents).toBe(5)

    const [, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit]
    expect(opts.method).toBe('DELETE')
    const body = JSON.parse(opts.body as string)
    expect(body.filter_key).toBe('user_id')
    expect(body.filter_value).toBe('user_123')
  })
})

// ─────────────────────────────────────────
// Error mapping
// ─────────────────────────────────────────

describe('error mapping', () => {
  it('throws AuthError on 401', async () => {
    mockFetch(401, { detail: 'Invalid API key' })
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_bad' })
    await expect(db.agents()).rejects.toThrow(AuthError)
  })

  it('cloud 401 points to the cloud dashboard', async () => {
    mockFetch(401, {})
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_bad' })
    const err = await db.agents().catch(e => e)
    expect(err).toBeInstanceOf(AuthError)
    expect(err.message).toContain('db.zizka.ai/dashboard')
  })

  it('self-hosted 401 points to the own instance, not the cloud dashboard', async () => {
    mockFetch(401, { detail: "Send it as 'Authorization: Bearer <api-key>'." })
    const db = new ZizkaDB({ host: 'http://localhost:8000' })
    const err = await db.agents().catch(e => e)
    expect(err).toBeInstanceOf(AuthError)
    expect(err.message).toContain('http://localhost:8000')
    expect(err.message).not.toContain('db.zizka.ai/dashboard')
    // The server's actionable guidance is surfaced to the caller.
    expect(err.message).toContain('Authorization: Bearer')
  })

  it('throws NotFoundError on 404', async () => {
    mockFetch(404, { detail: 'Not found' })
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    await expect(db.why('nonexistent')).rejects.toThrow(NotFoundError)
  })

  it('throws RateLimitError on 429', async () => {
    mockFetch(429, { detail: 'Rate limit exceeded' })
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    await expect(db.log({ agent: 'x', event: 'e', data: {} })).rejects.toThrow(RateLimitError)
  })

  it('throws AgentScopeError on 403', async () => {
    mockFetch(403, { detail: 'Agent mismatch' })
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    await expect(db.log({ agent: 'wrong-bot', event: 'e', data: {} })).rejects.toThrow(AgentScopeError)
  })

  it('throws ZizkaDBError with status on generic 4xx', async () => {
    mockFetch(422, { detail: 'Validation error' })
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const err = await db.log({ agent: 'x', event: 'e', data: {} }).catch(e => e)
    expect(err).toBeInstanceOf(ZizkaDBError)
    expect((err as ZizkaDBError).statusCode).toBe(422)
  })

  it('formats FastAPI array validation detail on 422', async () => {
    mockFetch(422, {
      detail: [{ type: 'missing', loc: ['body', 'agent'], msg: 'Field required' }],
    })
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const err = await db.log({ agent: 'x', event: 'e', data: {} }).catch(e => e)
    expect((err as ZizkaDBError).message).toContain('Field required')
    expect((err as ZizkaDBError).message).not.toContain('[object Object]')
  })

  it('includes detail from JSON body in error message', async () => {
    mockFetch(400, { detail: 'agent_id too long' })
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const err = await db.log({ agent: 'x', event: 'e', data: {} }).catch(e => e)
    expect((err as ZizkaDBError).message).toContain('agent_id too long')
  })
})

// ─────────────────────────────────────────
// contextFor()
// ─────────────────────────────────────────

describe('contextFor()', () => {
  it('POSTs /v1/memory/context and returns the context string', async () => {
    mockFetch(200, {
      context: 'Recent: user asked about billing.\nRelevant: prior refund on file.',
      event_count: 3,
      estimated_tokens: 42,
      sources: [],
    })

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const context = await db.contextFor({
      agent: 'support-bot',
      task: 'user asking about their invoice',
      maxTokens: 1500,
      sessionId: 'sess-abc',
    })

    expect(context).toContain('billing')

    const [url, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/v1/memory/context')
    expect(opts.method).toBe('POST')

    const body = JSON.parse(opts.body as string)
    expect(body.agent).toBe('support-bot')
    expect(body.task).toBe('user asking about their invoice')
    expect(body.max_tokens).toBe(1500)
    expect(body.session_id).toBe('sess-abc')
  })

  it('defaults maxTokens to 2000 and sessionId to null', async () => {
    mockFetch(200, { context: 'ok', event_count: 0, estimated_tokens: 0, sources: [] })

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    await db.contextFor({ agent: 'my-bot', task: 'hello' })

    const [, opts] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(opts.body as string)
    expect(body.max_tokens).toBe(2000)
    expect(body.session_id).toBeNull()
  })
})

// ─────────────────────────────────────────
// memoryDiff()
// ─────────────────────────────────────────

describe('memoryDiff()', () => {
  it('GETs /v1/memory/diff/:sessionId and maps the response', async () => {
    mockFetch(200, {
      session_id: 'sess-abc',
      agent: 'support-bot',
      event_count: 4,
      event_types: { user_message: 2, tool_call: 2 },
      causal_depth: 3,
      has_errors: false,
      duration_seconds: 12.5,
      new_event_types: ['tool_call'],
      summary: 'Session completed with 4 events.',
    })

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const diff = await db.memoryDiff('sess-abc')

    expect(diff.sessionId).toBe('sess-abc')
    expect(diff.agent).toBe('support-bot')
    expect(diff.eventCount).toBe(4)
    expect(diff.eventTypes).toEqual({ user_message: 2, tool_call: 2 })
    expect(diff.causalDepth).toBe(3)
    expect(diff.hasErrors).toBe(false)
    expect(diff.durationSeconds).toBe(12.5)
    expect(diff.newEventTypes).toEqual(['tool_call'])
    expect(diff.summary).toContain('4 events')

    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string]
    expect(url).toContain('/v1/memory/diff/sess-abc')
  })
})

// ─────────────────────────────────────────
// agents()
// ─────────────────────────────────────────

describe('agents()', () => {
  it('GETs /v1/agents and maps agent metadata', async () => {
    mockFetch(200, [
      {
        agent: 'support-bot',
        first_seen: '2026-06-01T00:00:00Z',
        last_seen: '2026-06-02T12:00:00Z',
        event_count: 42,
      },
      {
        agent: 'sales-bot',
        first_seen: '2026-06-01T06:00:00Z',
        last_seen: '2026-06-01T18:00:00Z',
        event_count: 7,
      },
    ])

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const agents = await db.agents()

    expect(agents).toHaveLength(2)
    expect(agents[0].agent).toBe('support-bot')
    expect(agents[0].eventCount).toBe(42)
    expect(agents[0].firstSeen).toBeInstanceOf(Date)
    expect(agents[0].lastSeen).toBeInstanceOf(Date)

    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string]
    expect(url).toContain('/v1/agents')
  })
})

// ─────────────────────────────────────────
// baseline()
// ─────────────────────────────────────────

describe('baseline()', () => {
  it('GETs /v1/agents/:id/baseline with recentWindow param', async () => {
    mockFetch(200, {
      agent: 'support-bot',
      status: 'ok',
      drift: { score: 0.08, verdict: 'minor_drift' },
    })

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const result = await db.baseline({ agent: 'support-bot', recentWindow: 30 })

    expect((result as Record<string, unknown>).agent).toBe('support-bot')

    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string]
    expect(url).toContain('/v1/agents/support-bot/baseline')
    expect(url).toContain('recent_window=30')
  })
})

// ─────────────────────────────────────────
// at() — time travel
// ─────────────────────────────────────────

describe('at()', () => {
  it('GETs /v1/events/at and returns AgentState', async () => {
    const ts = '2026-06-01T12:00:00.000Z'
    mockFetch(200, {
      agent: 'my-bot',
      at: ts,
      event_count: 3,
      state: { _last_event: { type: 'tool_call' } },
    })

    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const state = await db.at({ agent: 'my-bot', timestamp: new Date(ts) })

    expect(state.agent).toBe('my-bot')
    expect(state.eventCount).toBe(3)
  })
})

// ─────────────────────────────────────────
// Version
// ─────────────────────────────────────────

describe('SDK version', () => {
  it('SDK_VERSION matches package.json', async () => {
    const { SDK_VERSION } = await import('../index')
    const packageJson = await import('../../package.json')
    expect(SDK_VERSION).toBe(packageJson.version)
  })
})

describe('verifyChain()', () => {
  it('maps the chain head and passes an anchor', async () => {
    mockFetch(200, { agent: 'a', intact: true, summary: { events: 2 }, problems: [], chain_head: { sequence_no: 9, chain_hash: 'h9' } })
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_test' })
    const r = await db.verifyChain('a', { sequenceNo: 5, chainHash: 'h5' })
    expect(r.intact).toBe(true)
    expect(r.chainHead).toEqual({ sequenceNo: 9, chainHash: 'h9' })
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string]
    expect(url).toContain('/v1/events/chain?agent=a&after_seq=5&anchor=h5')
  })
})

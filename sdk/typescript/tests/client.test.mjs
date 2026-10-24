/**
 * ZizkaDB TypeScript SDK — Vitest coverage.
 *
 * No external services.  Mock fetch, disable telemetry, and test that the
 * SDK constructs expected request shapes and maps errors correctly.
 *
 * Uses the package's existing Vitest test runner.
 */

import { afterAll, afterEach, beforeEach, expect, it } from 'vitest'

// Save the original fetch so we can restore it after all tests finish.
const _originalFetch = globalThis.fetch

// Disable telemetry before loading the SDK.
process.env.ZIZKADB_TELEMETRY = 'false'

import { AgentScopeError, AuthError, ZizkaDB, ZizkaDBError } from '../src/index.ts'

// The client reads ZIZKADB_HOST / AGENTDB_HOST when no host is passed; a
// developer's exported values must not change which server a test talks to.
const HOST_VARS = ['ZIZKADB_HOST', 'AGENTDB_HOST']
let savedHostVars = {}
beforeEach(() => {
  savedHostVars = Object.fromEntries(HOST_VARS.map((k) => [k, process.env[k]]))
  for (const k of HOST_VARS) delete process.env[k]
})
afterEach(() => {
  for (const k of HOST_VARS) {
    if (savedHostVars[k] === undefined) delete process.env[k]
    else process.env[k] = savedHostVars[k]
  }
})


function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}


it('log posts the expected event payload', async () => {
  const calls = []

  globalThis.fetch = async (url, init) => {
    calls.push({ url, init })
    return jsonResponse({
      event_id: '00000000-0000-0000-0000-000000000001',
      timestamp: '2026-06-19T00:00:00Z',
      sequence_no: 1,
      checksum: 'abc123',
    })
  }

  const db = new ZizkaDB({
    apiKey: 'zizkadb_live_test',
    host: 'https://example.test',
  })

  const result = await db.log({
    agent: 'test-agent',
    event: 'tool_call',
    data: { tool: 'search' },
    parentId: 'parent-1',
    sessionId: 'session-1',
  })

  expect(calls).toHaveLength(1)
  expect(calls[0].url).toBe('https://example.test/v1/events')
  expect(calls[0].init.method).toBe('POST')
  expect(calls[0].init.headers.Authorization).toBe('Bearer zizkadb_live_test')

  const body = JSON.parse(calls[0].init.body)
  expect(body.agent).toBe('test-agent')
  expect(body.event).toBe('tool_call')
  expect(body.data).toEqual({ tool: 'search' })
  expect(body.parent_id).toBe('parent-1')
  expect(body.session_id).toBe('session-1')
  expect(result.eventId).toBe('00000000-0000-0000-0000-000000000001')
})


it('403 responses raise AgentScopeError', async () => {
  globalThis.fetch = async () =>
    jsonResponse({ detail: 'wrong agent' }, 403)

  const db = new ZizkaDB({
    apiKey: 'zizkadb_live_test',
    host: 'https://example.test',
  })

  await expect(db.query({ agent: 'wrong-agent' })).rejects.toBeInstanceOf(AgentScopeError)
})


it('401 responses raise AuthError', async () => {
  globalThis.fetch = async () =>
    jsonResponse({ detail: 'bad key' }, 401)

  const db = new ZizkaDB({
    apiKey: 'zizkadb_live_test',
    host: 'https://example.test',
  })

  await expect(db.query({ agent: 'test-agent' })).rejects.toBeInstanceOf(AuthError)
})


it('500 responses raise ZizkaDBError', async () => {
  globalThis.fetch = async () =>
    jsonResponse({ detail: 'boom' }, 500)

  const db = new ZizkaDB({
    apiKey: 'zizkadb_live_test',
    host: 'https://example.test',
  })

  await expect(db.query({ agent: 'test-agent' })).rejects.toBeInstanceOf(ZizkaDBError)
})


it('localhost auto-injects dev key', async () => {
  delete process.env.ZIZKADB_API_KEY
  delete process.env.AGENTDB_API_KEY
  delete process.env.DEV_API_KEY

  let authHeader

  globalThis.fetch = async (url, init) => {
    authHeader = init.headers.Authorization
    return jsonResponse({ event_id: 'evt-1', timestamp: '2026-01-01T00:00:00Z', sequence_no: 1, checksum: 'x' })
  }

  const db = new ZizkaDB({ host: 'http://localhost:8000' })
  await db.log({ agent: 'test-agent', event: 'tool_call', data: {} })

  expect(authHeader).toBe('Bearer zizkadb_dev_local')
})


// Restore original fetch after all tests run.
afterAll(() => {
  globalThis.fetch = _originalFetch
})


function captureEvents() {
  const bodies = []
  globalThis.fetch = async (_url, init) => {
    bodies.push(JSON.parse(init.body))
    return jsonResponse({
      event_id: `00000000-0000-0000-0000-00000000000${bodies.length}`,
      timestamp: '2026-06-19T00:00:00Z',
      sequence_no: bodies.length,
      checksum: 'abc123',
    })
  }
  return bodies
}

it('logTool / logToolResult name the tool', async () => {
  const bodies = captureEvents()
  const db = new ZizkaDB({ apiKey: 'zizkadb_live_test', host: 'https://example.test' })

  await db.logTool({ agent: 'bot', tool: 'lookup_order', args: { orderId: 'ORD-1' } })
  await db.logToolResult({ agent: 'bot', tool: 'lookup_order', result: { ok: true }, durationMs: 12 })
  await db.logToolResult({ agent: 'bot', tool: 'lookup_order', error: new TypeError('bad') })

  expect(bodies[0].event).toBe('tool_call')
  expect(bodies[0].data).toEqual({ tool: 'lookup_order', args: { orderId: 'ORD-1' } })
  expect(bodies[1].event).toBe('tool_result')
  expect(bodies[1].data).toEqual({ tool: 'lookup_order', result: { ok: true }, duration_ms: 12 })
  expect(bodies[2].event).toBe('tool_error')
  expect(bodies[2].data).toEqual({ tool: 'lookup_order', error: 'bad', type: 'TypeError' })
})

it('logApi names the call and drops secrets from the url', async () => {
  const bodies = captureEvents()
  const db = new ZizkaDB({ apiKey: 'zizkadb_live_test', host: 'https://example.test' })

  await db.logApi({
    agent: 'bot',
    method: 'post',
    url: 'https://user:pw@api.stripe.com:8443/v1/refunds?api_key=sk_live_secret#frag',
    status: 502,
  })
  await db.logApi({ agent: 'bot', method: 'GET', url: '/internal/orders?token=abc', name: 'orders.list' })

  expect(bodies[0].event).toBe('api_call')
  expect(bodies[0].data).toEqual({
    api: 'POST api.stripe.com:8443/v1/refunds',
    method: 'POST',
    url: 'https://api.stripe.com:8443/v1/refunds',
    status: 502,
  })
  expect(JSON.stringify(bodies[0])).not.toMatch(/secret|pw@/)
  expect(bodies[1].data).toEqual({ api: 'orders.list', method: 'GET', url: '/internal/orders' })
})


async function firstRequestUrl(config) {
  const urls = []
  globalThis.fetch = async (url) => {
    urls.push(String(url))
    return jsonResponse({ event_id: 'e', timestamp: 't', sequence_no: 1, checksum: 'c' })
  }
  const db = new ZizkaDB(config)
  await db.log({ agent: 'a', event: 'user_message', data: {} })
  return urls[0]
}

function withHostEnv(value, fn) {
  const saved = process.env.ZIZKADB_HOST
  if (value === undefined) delete process.env.ZIZKADB_HOST
  else process.env.ZIZKADB_HOST = value
  return fn().finally(() => {
    if (saved === undefined) delete process.env.ZIZKADB_HOST
    else process.env.ZIZKADB_HOST = saved
  })
}

it('an apiKey with ZIZKADB_HOST set goes to that host, not Cloud', () =>
  withHostEnv('https://zizka.example.com/', async () => {
    const url = await firstRequestUrl({ apiKey: 'zizkadb_live_x' })
    expect(url.startsWith('https://zizka.example.com/v1/')).toBe(true)
  }))

it('an explicit host wins over ZIZKADB_HOST', () =>
  withHostEnv('https://env.example.com', async () => {
    const url = await firstRequestUrl({ apiKey: 'zizkadb_live_x', host: 'https://arg.example.com' })
    expect(url.startsWith('https://arg.example.com/v1/')).toBe(true)
  }))

it('an apiKey alone still means Cloud', () =>
  withHostEnv(undefined, async () => {
    const url = await firstRequestUrl({ apiKey: 'zizkadb_live_x' })
    expect(url).toMatch(/^https:\/\/db\.zizka\.ai\//)
  }))

it('the legacy AGENTDB_HOST is honoured when ZIZKADB_HOST is unset', async () => {
  const saved = process.env.AGENTDB_HOST
  process.env.AGENTDB_HOST = 'https://legacy.example.com'
  try {
    await withHostEnv(undefined, async () => {
      const url = await firstRequestUrl({ apiKey: 'zizkadb_live_x' })
      expect(url.startsWith('https://legacy.example.com/v1/')).toBe(true)
    })
  } finally {
    if (saved === undefined) delete process.env.AGENTDB_HOST
    else process.env.AGENTDB_HOST = saved
  }
})

it('a blank host means unset, not a relative URL', () =>
  withHostEnv(undefined, async () => {
    const url = await firstRequestUrl({ apiKey: 'zizkadb_live_x', host: '  ' })
    expect(url).toMatch(/^https:\/\/db\.zizka\.ai\//)
  }))

it('constructs without a process global (browsers, edge runtimes)', () => {
  const realProcess = globalThis.process
  try {
    globalThis.process = undefined
    // Key only (host lookup) and local host only (key and dev-key lookup).
    expect(() => new ZizkaDB({ apiKey: 'zizkadb_live_x' })).not.toThrow()
    expect(() => new ZizkaDB({ host: 'http://localhost:8000' })).not.toThrow()
  } finally {
    globalThis.process = realProcess
  }
})

it('constructs and logs with a process shim that has no env (bundler polyfills)', async () => {
  const realProcess = globalThis.process
  globalThis.fetch = async () => jsonResponse({ event_id: 'e', timestamp: 't', sequence_no: 1, checksum: 'c' })
  try {
    globalThis.process = {}
    const db = new ZizkaDB({ apiKey: 'zizkadb_live_x', host: 'http://localhost:8000' })
    // log() reads ZIZKADB_QUIET / LOG_HINTS / STRICT: none may throw without env.
    await expect(db.log({ agent: 'a', event: 'user_message', data: {} })).resolves.toBeTruthy()
  } finally {
    globalThis.process = realProcess
  }
})

it('a blank ZIZKADB_HOST falls through to AGENTDB_HOST', async () => {
  process.env.ZIZKADB_HOST = '  '
  process.env.AGENTDB_HOST = 'https://legacy.example.com'
  const url = await firstRequestUrl({ apiKey: 'zizkadb_live_x' })
  expect(url.startsWith('https://legacy.example.com/v1/')).toBe(true)
})

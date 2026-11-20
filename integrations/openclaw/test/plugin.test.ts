import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import plugin, { PLUGIN_ID, register } from '../src/index'
import { clip } from '../src/recorder'
import { PLUGIN_VERSION } from '../src/telemetry'

// No real install pings from the test run.
process.env.ZIZKADB_TELEMETRY = 'false'

type Logged = { id: string; agent: string; event: string; data: Record<string, any>; parentId?: string; sessionId?: string; metadata?: Record<string, unknown> }

function fakeDb(opts: { failOn?: string; delayMs?: number } = {}) {
  const events: Logged[] = []
  let n = 0
  const db = {
    events,
    async log(o: Omit<Logged, 'id'>) {
      if (opts.delayMs) await new Promise((r) => setTimeout(r, Math.random() * opts.delayMs!))
      if (o.event === opts.failOn) throw new Error('server down')
      const id = `e${++n}`
      events.push({ id, ...o })
      return { eventId: id }
    },
    why: vi.fn(async (id: string, depth: number) => ({
      eventId: id,
      chainComplete: true,
      chain: [{ eventId: id, event: 'assistant_response', timestamp: new Date(0), data: { text: 'hi' }, depth }],
    })),
    search: vi.fn(async (_o: unknown) => [{ eventId: 'e1', event: 'user_message', timestamp: new Date(0), sessionId: 's', data: { text: 'refund' } }]),
  }
  return db
}

function fakeApi(options: { config?: Record<string, unknown>; mode?: string; allowConversationAccess?: boolean } = {}) {
  const handlers = new Map<string, (event: any, ctx: any) => unknown>()
  const tools: any[] = []
  const logs = { warn: [] as string[], info: [] as string[] }
  const api = {
    id: PLUGIN_ID,
    registrationMode: options.mode ?? 'full',
    pluginConfig: options.config ?? { apiKey: 'zizkadb_live_test' },
    config: { plugins: { entries: { [PLUGIN_ID]: { hooks: { allowConversationAccess: options.allowConversationAccess ?? true } } } } },
    logger: { info: (m: string) => logs.info.push(m), warn: (m: string) => logs.warn.push(m), error: () => {} },
    on: (name: string, handler: any) => handlers.set(name, handler),
    registerTool: (tool: any, opts: any) => tools.push({ tool, opts }),
  }
  return { api, handlers, tools, logs, fire: (name: string, event: any, ctx: any = {}) => handlers.get(name)!(event, ctx) }
}

function setup(options: Parameters<typeof fakeApi>[0] & { db?: ReturnType<typeof fakeDb> } = {}) {
  const db = options.db ?? fakeDb()
  const t = fakeApi(options)
  const recorder = register(t.api as any, () => db as any)
  return { ...t, db, recorder: recorder! }
}

const ctx = { runId: 'run-1', sessionKey: 'agent:main:telegram:42', agentId: 'main', channelId: 'telegram' }

function oneTurn(fire: ReturnType<typeof fakeApi>['fire'], c = ctx, prefix = '') {
  fire('message_received', { content: `${prefix}Where is order 42?`, from: 'user-7', runId: c.runId }, { channelId: c.channelId, sessionKey: c.sessionKey, runId: c.runId })
  fire('after_tool_call', { toolName: 'lookup_order', params: { id: 42 }, runId: c.runId, toolCallId: 't1', result: { status: 'shipped' }, durationMs: 120.4 }, c)
  fire('after_tool_call', { toolName: 'send_email', params: { to: 'a@b.c' }, runId: c.runId, toolCallId: 't2', error: 'smtp timeout', durationMs: 3000 }, c)
  fire('llm_output', { runId: c.runId, sessionId: 'sid', provider: 'anthropic', model: 'claude-x', assistantTexts: ['Order 42', 'has shipped.'], usage: { input: 900, output: 40, cacheRead: 300, cacheWrite: 10, total: 1250 } }, c)
  fire('agent_end', { runId: c.runId, success: true, messages: [], durationMs: 2000 }, c)
  fire('message_sent', { to: 'user-7', content: `${prefix}Order 42 has shipped.`, success: true }, { channelId: c.channelId, sessionKey: c.sessionKey })
}

describe('a turn becomes one causal chain', () => {
  it('records each step linked to the one before it', async () => {
    const { fire, db, recorder } = setup()
    oneTurn(fire)
    await recorder.flush()
    const ev = db.events
    expect(ev.map((e) => e.event)).toEqual(['user_message', 'tool_call', 'tool_result', 'tool_call', 'tool_error', 'llm_end', 'assistant_response'])
    // linear chain: every event points at the one before it, except the tool outcome which points at its call
    expect(ev[0].parentId).toBeUndefined()
    for (let i = 1; i < ev.length; i++) expect(ev[i].parentId).toBe(ev[i - 1].id)
    expect(new Set(ev.map((e) => e.sessionId))).toEqual(new Set([ctx.sessionKey]))
    expect(new Set(ev.map((e) => e.agent))).toEqual(new Set(['openclaw']))
    expect(ev[0].data).toEqual({ text: 'Where is order 42?', from: 'user-7' })
    expect(ev[0].metadata).toEqual({ source: 'openclaw', channel: 'telegram' })
    expect(ev[1].metadata).toEqual({ source: 'openclaw', openclaw_agent: 'main', channel: 'telegram' })
    expect(ev[1].data).toEqual({ tool: 'lookup_order', tool_call_id: 't1', arguments: { id: 42 } })
    expect(ev[2].data).toEqual({ tool: 'lookup_order', tool_call_id: 't1', result: { status: 'shipped' }, duration_ms: 120 })
    expect(ev[4].data).toEqual({ tool: 'send_email', tool_call_id: 't2', error: 'smtp timeout', duration_ms: 3000 })
    expect(ev[5].data).toEqual({
      provider: 'anthropic',
      model: 'claude-x',
      output: 'Order 42\n\nhas shipped.',
      token_usage: { model: 'claude-x', input_tokens: 900, output_tokens: 40, cached_tokens: 300, cache_write_tokens: 10 },
    })
    expect(ev[6].data).toEqual({ text: 'Order 42 has shipped.', delivered: true })
  })

  it('keeps concurrent turns in separate chains', async () => {
    const { fire, db, recorder } = setup({ db: fakeDb({ delayMs: 15 }) })
    const a = { ...ctx, runId: 'run-a', sessionKey: 'sess-a' }
    const b = { ...ctx, runId: 'run-b', sessionKey: 'sess-b' }
    // interleave the two turns hook by hook
    fire('message_received', { content: 'A?', runId: 'run-a' }, a)
    fire('message_received', { content: 'B?', runId: 'run-b' }, b)
    fire('after_tool_call', { toolName: 'ta', params: {}, runId: 'run-a', result: 1 }, a)
    fire('after_tool_call', { toolName: 'tb', params: {}, runId: 'run-b', result: 2 }, b)
    fire('llm_output', { runId: 'run-b', assistantTexts: ['B!'] }, b)
    fire('llm_output', { runId: 'run-a', assistantTexts: ['A!'] }, a)
    await recorder.flush()
    for (const s of ['sess-a', 'sess-b']) {
      const chain = db.events.filter((e) => e.sessionId === s)
      expect(chain.map((e) => e.event)).toEqual(['user_message', 'tool_call', 'tool_result', 'llm_end'])
      for (let i = 1; i < chain.length; i++) expect(chain[i].parentId).toBe(chain[i - 1].id)
    }
  })

  it('takes the user message from llm_input when no channel message started the turn', async () => {
    const { fire, db, recorder } = setup()
    const c = { runId: 'cli-1', sessionKey: 'agent:main:explicit:x' }
    fire('llm_input', { runId: 'cli-1', prompt: 'What does note.txt say?', historyMessages: [], imagesCount: 0 }, c)
    fire('llm_input', { runId: 'cli-1', prompt: 'What does note.txt say?', historyMessages: [], imagesCount: 0 }, c) // retry attempt
    fire('after_tool_call', { toolName: 'read', params: { path: 'note.txt' }, runId: 'cli-1', result: 'hi' }, c)
    fire('llm_output', { runId: 'cli-1', prompt: 'What does note.txt say?', assistantTexts: ['It says hi.'] }, c)
    await recorder.flush()
    expect(db.events.map((e) => e.event)).toEqual(['user_message', 'tool_call', 'tool_result', 'llm_end'])
    expect(db.events[0].data).toEqual({ text: 'What does note.txt say?' })
    expect(db.events[1].parentId).toBe(db.events[0].id)
  })

  it('records one user message when a channel message also reaches llm_input', async () => {
    const { fire, db, recorder } = setup()
    fire('message_received', { content: 'hi', runId: 'r' }, { runId: 'r', sessionKey: 's' })
    fire('llm_input', { runId: 'r', prompt: '[telegram] hi', historyMessages: [], imagesCount: 0 }, { runId: 'r', sessionKey: 's' })
    fire('llm_output', { runId: 'r', prompt: 'hi', assistantTexts: ['hello'] }, { runId: 'r', sessionKey: 's' })
    await recorder.flush()
    expect(db.events.map((e) => [e.event, e.data.text ?? e.data.output])).toEqual([['user_message', 'hi'], ['llm_end', 'hello']])
  })

  it('links a reply without runId to the latest turn of its conversation', async () => {
    const { fire, db, recorder } = setup()
    fire('message_received', { content: 'hi', runId: 'run-9' }, { sessionKey: 'sk', runId: 'run-9' })
    fire('llm_output', { runId: 'run-9', assistantTexts: ['hello'] }, { sessionKey: 'sk', runId: 'run-9' })
    fire('message_sent', { content: 'hello', success: true }, { sessionKey: 'sk' })
    await recorder.flush()
    const [, llm, reply] = db.events
    expect(reply.event).toBe('assistant_response')
    expect(reply.parentId).toBe(llm.id)
  })
})

describe('privacy and failures', () => {
  it('captureContent: false keeps content out but still records what happened', async () => {
    const { fire, db, recorder } = setup({ config: { apiKey: 'k', captureContent: false } })
    oneTurn(fire)
    await recorder.flush()
    const all = JSON.stringify(db.events)
    for (const secret of ['Where is order 42', 'user-7', 'shipped', 'a@b.c', 'has shipped']) expect(all).not.toContain(secret)
    const llm = db.events.find((e) => e.event === 'llm_end')!
    expect(llm.data.model).toBe('claude-x')
    expect(llm.data.token_usage.input_tokens).toBe(900)
    expect(db.events.find((e) => e.event === 'tool_call')!.data.tool).toBe('lookup_order')
    expect(db.events.find((e) => e.event === 'tool_error')!.data.error).toBe('smtp timeout')
  })

  it('a failed write never throws and the chain continues from the last good event', async () => {
    const { fire, db, recorder, logs } = setup({ db: fakeDb({ failOn: 'tool_call' }) })
    oneTurn(fire)
    await recorder.flush()
    expect(logs.warn.some((m) => m.includes('could not record tool_call'))).toBe(true)
    const ev = db.events
    expect(ev.map((e) => e.event)).toEqual(['user_message', 'tool_result', 'tool_error', 'llm_end', 'assistant_response'])
    expect(ev[1].parentId).toBe(ev[0].id)
    expect(ev[3].parentId).toBe(ev[2].id)
  })

  it('records a failed run as agent_error, a successful one as nothing extra', async () => {
    const { fire, db, recorder } = setup()
    fire('agent_end', { runId: 'r', success: true, messages: [] }, { runId: 'r' })
    fire('agent_end', { runId: 'r2', success: false, error: 'model overloaded', durationMs: 50.6, messages: [] }, { runId: 'r2' })
    await recorder.flush()
    expect(db.events).toEqual([expect.objectContaining({ event: 'agent_error', data: { error: 'model overloaded', duration_ms: 51 } })])
  })

  it('clips huge values', () => {
    expect((clip('x'.repeat(20_000)) as string).length).toBeLessThan(8_100)
    expect(clip({ a: 1 })).toEqual({ a: 1 })
    expect(typeof clip({ big: 'y'.repeat(20_000) })).toBe('string')
  })
})

describe('plugin wiring', () => {
  it('observes only: every hook handler returns nothing', async () => {
    const { handlers, recorder } = setup()
    for (const [name, handler] of handlers) {
      if (name === 'gateway_stop') continue
      expect(handler({ toolName: 't', params: {}, success: true, content: '', messages: [] }, {})).toBeUndefined()
    }
    expect(await handlers.get('gateway_stop')!({}, {})).toBeUndefined()
    await recorder.flush()
  })

  it('registers nothing during discovery and metadata scans', () => {
    for (const mode of ['discovery', 'tool-discovery', 'setup-only', 'cli-metadata']) {
      const { handlers, tools, recorder } = setup({ mode })
      expect(recorder).toBeUndefined()
      expect(handlers.size + tools.length).toBe(0)
    }
  })

  it('says when model outputs are not allowed, and when nothing is configured', () => {
    expect(setup({ allowConversationAccess: false }).logs.info.join()).toContain('allowConversationAccess')
    expect(setup().logs.info.join()).not.toContain('allowConversationAccess')
    const env = { ...process.env }
    delete process.env.ZIZKADB_API_KEY
    delete process.env.ZIZKADB_HOST
    try {
      expect(setup({ config: {} }).logs.warn.join()).toContain('no apiKey or host')
    } finally {
      process.env = env
    }
  })

  it('a bad config disables recording without breaking the gateway', async () => {
    const t = fakeApi({ config: {} })
    const recorder = register(t.api as any, () => {
      throw new Error('Provide an apiKey')
    })!
    expect(() => oneTurn(t.fire)).not.toThrow()
    await recorder.flush()
    expect(t.logs.warn.filter((m) => m.includes('not recording'))).toHaveLength(1)
  })

  it('registers the tools as one optional, context-scoped factory', () => {
    const { tools } = setup({ config: { apiKey: 'k', agent: 'support' } })
    expect(tools).toHaveLength(1)
    expect(typeof tools[0].tool).toBe('function')
    expect(tools[0].opts).toEqual({ names: ['zizkadb_why', 'zizkadb_search'], optional: true })
    expect(tools[0].tool({ sessionKey: 'agent:main:telegram:1' }).map((t: any) => t.name)).toEqual(['zizkadb_why', 'zizkadb_search'])
    expect(tools[0].tool({})).toBeNull()
  })

  it('manifest, package.json and code agree', () => {
    const manifest = JSON.parse(readFileSync(new URL('../openclaw.plugin.json', import.meta.url), 'utf8'))
    const pkg = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
    expect(manifest.id).toBe(PLUGIN_ID)
    expect(JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8')).version).toBe(PLUGIN_VERSION)
    expect(plugin.id).toBe(PLUGIN_ID)
    const { tools } = setup()
    expect(manifest.contracts.tools.slice().sort()).toEqual(tools[0].opts.names.slice().sort())
    expect(Object.keys(manifest.configSchema.properties).sort()).toEqual(['agent', 'apiKey', 'captureContent', 'host'])
    expect(pkg.openclaw.extensions).toEqual(['./dist/index.js'])
    expect(pkg.files).toContain('openclaw.plugin.json')
  })
})

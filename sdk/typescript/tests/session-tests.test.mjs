/**
 * capture(), class-level instrument(), and `npx zizkadb-sdk test from-session`.
 */

process.env.ZIZKADB_TELEMETRY = 'false'

import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { capture, instrument } from '../src/index.ts'
import { main } from '../src/cli.ts'
import { buildSnapshot, renderTest, sessionTestName } from '../src/sessionTests.ts'

/** A fake OpenAI client class with resource classes on statics, like the real SDK. */
function makeOpenAIClass(replyFor) {
  class Completions {
    create(params) {
      return Promise.resolve(replyFor(params))
    }

    /** Like openai's helper: goes through create(). */
    stream(params) {
      return this.create({ ...params, stream: true })
    }
  }
  class Responses {
    create() {
      return Promise.resolve({ model: 'm', output: [] })
    }
  }
  class OpenAI {
    constructor() {
      this.chat = { completions: new Completions() }
      this.responses = new Responses()
    }
  }
  OpenAI.Chat = { Completions }
  OpenAI.Responses = Responses
  return OpenAI
}

const toolLoop = (params) => {
  if (params.messages.some((m) => m.role === 'tool')) return { model: 'm', choices: [{ message: { content: 'Order 42 ships tomorrow.' } }] }
  return { model: 'm', choices: [{ message: { content: null, tool_calls: [{ id: 'c1', function: { name: process.env.FAKE_TOOL ?? 'lookup_order', arguments: '{}' } }] } }] }
}

async function agent(OpenAI, text) {
  const client = new OpenAI() // created inside the agent, after capture() started
  const history = [{ role: 'user', content: text }]
  const first = await client.chat.completions.create({ model: 'm', messages: history })
  const call = first.choices[0].message.tool_calls[0]
  history.push({ role: 'assistant', tool_calls: [call] }, { role: 'tool', tool_call_id: call.id, content: 'ships tomorrow' })
  return (await client.chat.completions.create({ model: 'm', messages: history })).choices[0].message.content
}

describe('capture', () => {
  afterEach(() => { delete process.env.FAKE_TOOL })

  it('records calls of clients created inside the block when given the class', async () => {
    const OpenAI = makeOpenAIClass(toolLoop)
    let answer
    const run = await capture({ clients: [OpenAI] }, async () => { answer = await agent(OpenAI, 'Where is order 42?') })
    expect(answer).toBe('Order 42 ships tomorrow.')
    expect(run.toolCalls).toEqual(['lookup_order'])
    expect(run.events.map((e) => e.event)).toEqual(['user_message', 'llm_end', 'tool_call', 'tool_result', 'llm_end'])
    expect(run.outputs).toEqual(['Order 42 ships tomorrow.'])
    expect(run.errors).toEqual([])
    // the class is restored afterwards
    expect(Object.prototype.hasOwnProperty.call(OpenAI.Chat.Completions.prototype, 'create')).toBe(true)
    const again = await capture({ clients: [] }, () => agent(OpenAI, 'x'))
    expect(again.events).toEqual([])
  })

  it('keeps app instrumentation quiet during the block, and restores it', async () => {
    const OpenAI = makeOpenAIClass(toolLoop)
    const sent = []
    const app = instrument({ async log(o) { sent.push(o.event); return { eventId: `e${sent.length}` } } }, { agent: 'app', clients: [OpenAI] })
    const run = await capture({ clients: [OpenAI] }, () => agent(OpenAI, 'hi'))
    expect(run.toolCalls).toEqual(['lookup_order'])
    expect(sent).toEqual([])
    await agent(OpenAI, 'after the test block')
    await app.flush()
    app.uninstrument()
    expect(sent[0]).toBe('user_message')
  })

  it('records the .stream() helper through create()', async () => {
    const OpenAI = makeOpenAIClass(() => ({ [Symbol.asyncIterator]: async function* () { yield { model: 'm', choices: [{ delta: { content: 'streamed' } }] } } }))
    const run = await capture({ clients: [OpenAI] }, async () => {
      for await (const _ of await new OpenAI().chat.completions.stream({ model: 'm', messages: [{ role: 'user', content: 'hi' }] })) { /* read */ }
    })
    expect(run.outputs).toEqual(['streamed'])
  })
})

describe('test from-session', () => {
  const recorded = [
    { event: 'user_message', data: { text: 'Where is order 42?' } },
    { event: 'llm_end', data: { output: null } },
    { event: 'tool_call', data: { tool: 'lookup_order', arguments: { id: 42 } } },
    { event: 'tool_result', data: { result: 'ships tomorrow' } },
    { event: 'llm_end', data: { output: 'Order 42 ships tomorrow.' } },
  ]
  let dir
  afterEach(() => { if (dir) rmSync(dir, { recursive: true, force: true }) })

  it('summarises a session like the Python CLI', () => {
    const snap = buildSnapshot('sess-abc-123', 'support-bot', recorded)
    expect(snap.user_inputs).toEqual(['Where is order 42?'])
    expect(snap.expected_tool_calls).toEqual(['lookup_order'])
    expect(snap.recorded_final_output).toBe('Order 42 ships tomorrow.')
    expect(snap.event_count).toBe(5)
    expect(sessionTestName('sess-abc-123')).toBe('session_sess_abc_123')
    const withError = buildSnapshot('s', 'b', [{ event: 'tool_start', data: { tool_name: 'search' } }, { event: 'tool_error', data: { error: 'timeout' } }])
    expect(withError.expected_tool_calls).toEqual(['search'])
    expect(renderTest(withError, 'session_s')).toContain('ended with an error')
  })

  it('renders an import of the entry relative to the test folder', () => {
    const snap = buildSnapshot('s1', 'bot', recorded)
    const src = renderTest(snap, 'session_s1', { entry: 'src/agent.ts:answer', clients: ['openai', 'anthropic'], outDir: 'tests' })
    expect(src).toContain("import { answer as agent } from '../src/agent'")
    expect(src).toContain("import OpenAI from 'openai'")
    expect(src).toContain("import Anthropic from '@anthropic-ai/sdk'")
    expect(src).toContain('clients: [OpenAI, Anthropic]')
    expect(src).toContain('const AGENT_CONNECTED = true')
    expect(renderTest(snap, 'session_s1')).toContain('const AGENT_CONNECTED = false')
    expect(() => renderTest(snap, 'x', { entry: 'no-function' })).toThrow('--entry')
    expect(() => renderTest(snap, 'x', { clients: ['gemini'] })).toThrow('unknown --clients')
  })

  it('CLI writes both files, refuses to overwrite, and reports empty sessions', async () => {
    dir = mkdtempSync(join(tmpdir(), 'zdb-'))
    const lines = []
    const db = () => ({
      async *iterEvents(agent, opts) {
        expect([agent, opts.sessionId]).toEqual(['support-bot', 'sess-abc-123'])
        for (const e of [...recorded].reverse()) yield e // newest first, like the API
      },
    })
    const argv = ['test', 'from-session', 'sess-abc-123', '--agent', 'support-bot', '--out', dir]
    expect(await main(argv, { db, log: (s) => lines.push(s) })).toBe(0)
    expect(lines.join('\n')).toContain('tool path: lookup_order')
    const snap = JSON.parse(readFileSync(join(dir, 'zizkadb_sessions', 'session_sess_abc_123.json'), 'utf8'))
    expect(snap.user_inputs).toEqual(['Where is order 42?'])
    expect(readFileSync(join(dir, 'session_sess_abc_123.test.ts'), 'utf8')).toContain("from 'zizkadb-sdk'")
    expect(await main(argv, { db, log: (s) => lines.push(s) })).toBe(1)
    expect(lines.at(-1)).toContain('already exists')
    expect(await main([...argv, '--force'], { db, log: () => {} })).toBe(0)

    const empty = () => ({ async *iterEvents() {} })
    expect(await main(['test', 'from-session', 'nope', '--agent', 'b'], { db: empty, log: (s) => lines.push(s) })).toBe(1)
    expect(lines.at(-1)).toContain('No events')
    expect(await main(['test', 'from-session', 'x'], { db: empty, log: (s) => lines.push(s) })).toBe(2)
  })
})

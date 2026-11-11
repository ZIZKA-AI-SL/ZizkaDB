/**
 * Beyond OpenAI and Anthropic: OpenAI-compatible providers (DeepSeek, GLM,
 * Kimi) through instrument(), and any provider through the Vercel AI SDK
 * middleware.
 */

process.env.ZIZKADB_TELEMETRY = 'false'

import { describe, expect, it } from 'vitest'
import { capture, instrument, zizkadbMiddleware } from '../src/index.ts'

function fakeDb() {
  const events = []
  return { events, async log(o) { events.push(o); return { eventId: `evt-${events.length}` } } }
}

const clientWith = (replies) => ({ chat: { completions: { create: () => Promise.resolve(replies.shift()) } } })

describe('OpenAI-compatible providers', () => {
  it('DeepSeek: reasoning_content, cache hits and reasoning tokens', async () => {
    const db = fakeDb()
    const client = clientWith([{
      model: 'deepseek-reasoner',
      choices: [{ message: { content: '42 ships tomorrow.', reasoning_content: 'The lookup says tomorrow.' } }],
      usage: { prompt_tokens: 120, completion_tokens: 40, prompt_cache_hit_tokens: 96, completion_tokens_details: { reasoning_tokens: 31 } },
    }])
    const inst = instrument(db, { agent: 'bot', clients: [client] })
    await client.chat.completions.create({ model: 'deepseek-reasoner', messages: [{ role: 'user', content: 'order 42?' }] })
    await inst.flush()
    inst.uninstrument()
    expect(db.events[1].data.reasoning).toBe('The lookup says tomorrow.')
    expect(db.events[1].data.token_usage).toEqual({ model: 'deepseek-reasoner', input_tokens: 120, output_tokens: 40, cached_tokens: 96, reasoning_tokens: 31 })
  })

  it('Kimi cached_tokens and a GLM reasoning stream', async () => {
    const db = fakeDb()
    const glmStream = {
      async *[Symbol.asyncIterator]() {
        yield { model: 'glm-4.5', choices: [{ delta: { reasoning_content: 'Think ' } }] }
        yield { model: 'glm-4.5', choices: [{ delta: { reasoning_content: 'first.' } }] }
        yield { model: 'glm-4.5', choices: [{ delta: { content: 'Answer.' } }], usage: { prompt_tokens: 9, completion_tokens: 7, prompt_tokens_details: { cached_tokens: 4 } } }
      },
    }
    const client = clientWith([
      { model: 'kimi-k2', choices: [{ message: { content: 'Hello' } }], usage: { prompt_tokens: 50, completion_tokens: 2, cached_tokens: 32 } },
      glmStream,
    ])
    const inst = instrument(db, { agent: 'bot', clients: [client] })
    await client.chat.completions.create({ model: 'kimi-k2', messages: [{ role: 'user', content: 'hi' }] })
    for await (const _ of await client.chat.completions.create({ model: 'glm-4.5', stream: true, messages: [{ role: 'user', content: 'q' }] })) { /* read */ }
    await inst.flush()
    inst.uninstrument()
    expect(db.events[1].data.token_usage.cached_tokens).toBe(32)
    expect(db.events[3].data.reasoning).toBe('Think first.')
    expect(db.events[3].data.output).toBe('Answer.')
    expect(db.events[3].data.token_usage.cached_tokens).toBe(4)
  })
})

/** What the AI SDK passes a middleware: doGenerate/doStream, params.prompt, the model. */
const MODEL = { provider: 'google.generative-ai', modelId: 'gemini-2.5-flash' }

const userPrompt = (text) => [{ role: 'system', content: 'be brief' }, { role: 'user', content: [{ type: 'text', text }] }]

describe('Vercel AI SDK middleware', () => {
  it('records a multi-step tool loop (spec v3 content and nested usage)', async () => {
    const db = fakeDb()
    const mw = zizkadbMiddleware(db, { agent: 'bot' })
    expect(mw.specificationVersion).toBe('v3')
    const usage = { inputTokens: { total: 40, cacheRead: 8 }, outputTokens: { total: 9, reasoning: 3 } }
    const step1 = await mw.wrapGenerate({
      model: MODEL,
      params: { prompt: userPrompt('Where is order 42?') },
      doGenerate: async () => ({ content: [{ type: 'reasoning', text: 'Need the order.' }, { type: 'tool-call', toolCallId: 'tc_1', toolName: 'lookup_order', input: '{"id":42}' }], usage }),
    })
    expect(step1.content[1].toolName).toBe('lookup_order')
    await mw.wrapGenerate({
      model: MODEL,
      params: { prompt: [
        ...userPrompt('Where is order 42?'),
        { role: 'assistant', content: [{ type: 'tool-call', toolCallId: 'tc_1', toolName: 'lookup_order', input: { id: 42 } }] },
        { role: 'tool', content: [{ type: 'tool-result', toolCallId: 'tc_1', toolName: 'lookup_order', output: { type: 'text', value: 'ships tomorrow' } }] },
      ] },
      doGenerate: async () => ({ content: [{ type: 'text', text: 'Order 42 ships tomorrow.' }], usage }),
    })
    await new Promise((r) => setTimeout(r, 0))
    // flush by waiting for the queued writes
    for (let i = 0; i < 5 && db.events.length < 5; i++) await new Promise((r) => setTimeout(r, 5))

    expect(db.events.map((e) => e.event)).toEqual(['user_message', 'llm_end', 'tool_call', 'tool_result', 'llm_end'])
    expect(db.events[1].data).toMatchObject({ provider: 'google.generative-ai', model: 'gemini-2.5-flash', reasoning: 'Need the order.' })
    expect(db.events[1].data.token_usage).toEqual({ model: 'gemini-2.5-flash', input_tokens: 40, output_tokens: 9, cached_tokens: 8, reasoning_tokens: 3 })
    expect(db.events[2].data).toEqual({ tool: 'lookup_order', tool_call_id: 'tc_1', arguments: { id: 42 } })
    expect(db.events[3].parentId).toBe('evt-3')
    expect(db.events[3].data.result).toBe('ships tomorrow')
    expect(db.events[4].data.output).toBe('Order 42 ships tomorrow.')
  })

  it('records a stream and passes every part through', async () => {
    const db = fakeDb()
    const mw = zizkadbMiddleware(db, { agent: 'bot' })
    const parts = [
      { type: 'stream-start', warnings: [] },
      { type: 'response-metadata', modelId: 'deepseek-chat' },
      { type: 'reasoning-delta', id: 'r', delta: 'hmm' },
      { type: 'text-delta', id: 't', delta: 'Hello ' },
      { type: 'text-delta', id: 't', delta: 'there.' },
      { type: 'tool-call', toolCallId: 'tc_9', toolName: 'search', input: '{"q":"x"}' },
      { type: 'finish', finishReason: 'stop', usage: { inputTokens: 7, outputTokens: 3, cachedInputTokens: 2 } },
    ]
    const res = await mw.wrapStream({
      model: { provider: 'deepseek.chat', modelId: 'deepseek-chat' },
      params: { prompt: userPrompt('hi') },
      doStream: async () => ({ stream: new ReadableStream({ start(c) { parts.forEach((p) => c.enqueue(p)); c.close() } }), request: { body: 'x' } }),
    })
    expect(res.request).toEqual({ body: 'x' })
    const seen = []
    for await (const p of res.stream) seen.push(p)
    expect(seen).toEqual(parts)
    for (let i = 0; i < 5 && db.events.length < 3; i++) await new Promise((r) => setTimeout(r, 5))
    expect(db.events.map((e) => e.event)).toEqual(['user_message', 'llm_end', 'tool_call'])
    expect(db.events[1].data).toMatchObject({ output: 'Hello there.', reasoning: 'hmm', streamed: true })
    expect(db.events[1].data.token_usage).toEqual({ model: 'deepseek-chat', input_tokens: 7, output_tokens: 3, cached_tokens: 2 })
  })

  it('handles AI SDK 4 results (spec v1) and errors', async () => {
    const db = fakeDb()
    const mw = zizkadbMiddleware(db, { agent: 'bot' })
    await mw.wrapGenerate({
      model: { provider: 'mistral.chat', modelId: 'mistral-large' },
      params: { prompt: userPrompt('v4') },
      doGenerate: async () => ({ text: 'old style', toolCalls: [{ toolCallId: 'a', toolName: 'calc', args: '{"x":1}' }], usage: { promptTokens: 5, completionTokens: 2 } }),
    })
    await expect(mw.wrapGenerate({ model: MODEL, params: { prompt: userPrompt('boom') }, doGenerate: async () => { throw new Error('quota exceeded') } })).rejects.toThrow('quota')
    for (let i = 0; i < 5 && db.events.length < 5; i++) await new Promise((r) => setTimeout(r, 5))
    expect(db.events.map((e) => e.event)).toEqual(['user_message', 'llm_end', 'tool_call', 'user_message', 'llm_error'])
    expect(db.events[1].data.token_usage).toMatchObject({ input_tokens: 5, output_tokens: 2 })
    expect(db.events[2].data.arguments).toEqual({ x: 1 })
    expect(db.events[4].data.error).toContain('quota exceeded')
  })

  it('stopping a stream early is recorded as truncated', async () => {
    const db = fakeDb()
    const mw = zizkadbMiddleware(db, { agent: 'bot' })
    const res = await mw.wrapStream({
      model: MODEL,
      params: { prompt: userPrompt('long') },
      doStream: async () => ({ stream: new ReadableStream({ pull(c) { c.enqueue({ type: 'text-delta', delta: 'more ' }) } }) }),
    })
    const reader = res.stream.getReader()
    await reader.read()
    await reader.cancel()
    for (let i = 0; i < 5 && db.events.length < 2; i++) await new Promise((r) => setTimeout(r, 5))
    expect(db.events[1].data).toMatchObject({ truncated: true, output: 'more ' })
  })

  it('inside capture() the middleware records to the test run, not to ZizkaDB', async () => {
    const db = fakeDb()
    const mw = zizkadbMiddleware(db, { agent: 'bot' })
    const run = await capture({}, () => mw.wrapGenerate({
      model: MODEL,
      params: { prompt: userPrompt('in a test') },
      doGenerate: async () => ({ content: [{ type: 'tool-call', toolCallId: 't', toolName: 'search', input: '{}' }] }),
    }))
    expect(run.toolCalls).toEqual(['search'])
    expect(db.events).toEqual([])
  })
})

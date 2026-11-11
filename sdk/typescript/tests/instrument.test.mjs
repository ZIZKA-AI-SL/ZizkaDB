/**
 * instrument(): auto-capture of OpenAI / Anthropic calls with causal links.
 * Stand-in clients shaped like the openai and @anthropic-ai/sdk packages.
 */

process.env.ZIZKADB_TELEMETRY = 'false'

import { describe, expect, it, vi } from 'vitest'
import { instrument } from '../src/index.ts'

function fakeDb() {
  const events = []
  return {
    events,
    async log(opts) {
      events.push(opts)
      return { eventId: `evt-${events.length}` }
    },
  }
}

/** A promise with an extra method, like openai's APIPromise. */
function apiPromise(value) {
  const p = value instanceof Error ? Promise.reject(value) : Promise.resolve(value)
  p.withResponse = () => 'extras still work'
  return p
}

function openaiClient(replies) {
  return {
    chat: { completions: { create: vi.fn(() => apiPromise(replies.chat.shift())) } },
    responses: { create: vi.fn(() => apiPromise(replies.responses.shift())) },
  }
}

const chatReply = (text, toolCalls = []) => ({
  model: 'gpt-test',
  choices: [{ message: { content: text, tool_calls: toolCalls } }],
  usage: { prompt_tokens: 12, completion_tokens: 5, prompt_tokens_details: { cached_tokens: 2 } },
})

describe('instrument', () => {
  it('links an OpenAI chat tool loop and records each tool result once', async () => {
    const db = fakeDb()
    const client = openaiClient({
      chat: [
        chatReply(null, [{ id: 'call_1', function: { name: 'lookup_order', arguments: '{"id":42}' } }]),
        chatReply('Your order ships tomorrow.'),
        chatReply("You're welcome."),
      ],
    })
    const inst = instrument(db, { agent: 'bot', clients: [client] })
    const history = [{ role: 'user', content: 'Where is order 42?' }]
    await client.chat.completions.create({ model: 'gpt-test', messages: history })
    history.push({ role: 'assistant', tool_calls: [{ id: 'call_1' }] }, { role: 'tool', tool_call_id: 'call_1', content: 'ships tomorrow' })
    await client.chat.completions.create({ model: 'gpt-test', messages: history })
    history.push({ role: 'assistant', content: 'Your order ships tomorrow.' }, { role: 'user', content: 'thanks' })
    await client.chat.completions.create({ model: 'gpt-test', messages: history })
    await inst.flush()
    inst.uninstrument()

    expect(db.events.map((e) => e.event)).toEqual([
      'user_message', 'llm_end', 'tool_call',
      'tool_result', 'llm_end',
      'user_message', 'llm_end',
    ])
    const e = db.events
    expect(e[1].parentId).toBe('evt-1')
    expect(e[2].parentId).toBe('evt-2')
    expect(e[2].data.arguments).toEqual({ id: 42 })
    expect(e[3].parentId).toBe('evt-3')
    expect(e[4].parentId).toBe('evt-4')
    expect(e[1].data.token_usage).toEqual({ model: 'gpt-test', input_tokens: 12, output_tokens: 5, cached_tokens: 2 })
    expect(e.every((x) => x.agent === 'bot' && x.metadata.source === 'zizkadb.instrument')).toBe(true)
  })

  it('returns the client promise unchanged (APIPromise extras keep working)', async () => {
    const db = fakeDb()
    const client = openaiClient({ chat: [chatReply('hi')] })
    const inst = instrument(db, { agent: 'bot', clients: [client] })
    const result = client.chat.completions.create({ model: 'gpt-test', messages: [{ role: 'user', content: 'hi' }] })
    expect(result.withResponse()).toBe('extras still work')
    expect((await result).choices[0].message.content).toBe('hi')
    await inst.flush()
    inst.uninstrument()
  })

  it('records the Responses API with function calls and their outputs', async () => {
    const db = fakeDb()
    const client = openaiClient({
      responses: [
        { model: 'gpt-resp', output: [{ type: 'function_call', call_id: 'fc_1', name: 'lookup_order', arguments: '{"id":42}' }],
          usage: { input_tokens: 50, output_tokens: 9, input_tokens_details: { cached_tokens: 4 }, output_tokens_details: { reasoning_tokens: 3 } } },
        { model: 'gpt-resp', output: [{ type: 'message', content: [{ type: 'output_text', text: 'Order 42 ships tomorrow.' }] }] },
      ],
    })
    const inst = instrument(db, { agent: 'bot', clients: [client] })
    await client.responses.create({ model: 'gpt-resp', input: 'Where is order 42?' })
    await client.responses.create({ model: 'gpt-resp', previous_response_id: 'r1', input: [{ type: 'function_call_output', call_id: 'fc_1', output: 'ships tomorrow' }] })
    await inst.flush()
    inst.uninstrument()

    expect(db.events.map((e) => e.event)).toEqual(['user_message', 'llm_end', 'tool_call', 'tool_result', 'llm_end'])
    expect(db.events[0].data.text).toBe('Where is order 42?')
    expect(db.events[3].parentId).toBe('evt-3')
    expect(db.events[4].data.output).toBe('Order 42 ships tomorrow.')
    expect(db.events[1].data.token_usage.reasoning_tokens).toBe(3)
  })

  it('records Anthropic tool_use and tool_result blocks', async () => {
    const db = fakeDb()
    const replies = [
      { model: 'claude-test', content: [{ type: 'text', text: 'Checking.' }, { type: 'tool_use', id: 'tu_1', name: 'search', input: { q: 'refund' } }], usage: { input_tokens: 30, output_tokens: 8 } },
      { model: 'claude-test', content: [{ type: 'text', text: 'Refunds take 5 days.' }], usage: { input_tokens: 40, output_tokens: 6 } },
    ]
    const client = { messages: { create: vi.fn(() => apiPromise(replies.shift())) } }
    const inst = instrument(db, { agent: 'bot', clients: [client] })
    await client.messages.create({ model: 'claude-test', messages: [{ role: 'user', content: 'refund time?' }] })
    await client.messages.create({
      model: 'claude-test',
      messages: [
        { role: 'user', content: 'refund time?' },
        { role: 'assistant', content: [{ type: 'tool_use', id: 'tu_1' }] },
        { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'tu_1', content: '5 days' }] },
      ],
    })
    await inst.flush()
    inst.uninstrument()
    expect(db.events.map((e) => e.event)).toEqual(['user_message', 'llm_end', 'tool_call', 'tool_result', 'llm_end'])
    expect(db.events[2].data.tool).toBe('search')
    expect(db.events[3].data.result).toBe('5 days')
  })

  it('records an error, and the caller still gets the original rejection', async () => {
    const db = fakeDb()
    const client = openaiClient({ chat: [new Error('rate limited')] })
    const inst = instrument(db, { agent: 'bot', clients: [client] })
    await expect(client.chat.completions.create({ model: 'gpt-test', messages: [{ role: 'user', content: 'hi' }] })).rejects.toThrow('rate limited')
    await inst.flush()
    inst.uninstrument()
    expect(db.events.map((e) => e.event)).toEqual(['user_message', 'llm_error'])
    expect(db.events[1].parentId).toBe('evt-1')
    expect(db.events[1].data.error).toContain('rate limited')
  })

  it('never breaks the call when ZizkaDB is unreachable', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const db = { log: async () => { throw new Error('down') } }
    const client = openaiClient({ chat: [chatReply('still works')] })
    const inst = instrument(db, { agent: 'bot', clients: [client] })
    const resp = await client.chat.completions.create({ model: 'gpt-test', messages: [{ role: 'user', content: 'hi' }] })
    await inst.flush()
    inst.uninstrument()
    expect(resp.choices[0].message.content).toBe('still works')
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('captureContent: false keeps the links but not the text', async () => {
    const db = fakeDb()
    const client = openaiClient({ chat: [chatReply('secret answer')] })
    const inst = instrument(db, { agent: 'bot', clients: [client], captureContent: false })
    await client.chat.completions.create({ model: 'gpt-test', messages: [{ role: 'user', content: 'secret question' }] })
    await inst.flush()
    inst.uninstrument()
    expect(db.events[0].data.text).toBeNull()
    expect(db.events[1].data.output).toBeNull()
    expect(db.events[1].parentId).toBe('evt-1')
  })

  it('records streaming calls without their output', async () => {
    const db = fakeDb()
    const client = openaiClient({ chat: [{ [Symbol.asyncIterator]: async function* () {} }] })
    const inst = instrument(db, { agent: 'bot', clients: [client] })
    await client.chat.completions.create({ model: 'gpt-test', stream: true, messages: [{ role: 'user', content: 'hi' }] })
    await inst.flush()
    inst.uninstrument()
    expect(db.events[1].data.streamed).toBe(true)
    expect('output' in db.events[1].data).toBe(false)
  })

  it('uninstrument restores the original methods', () => {
    const client = openaiClient({ chat: [] })
    const original = client.chat.completions.create
    const inst = instrument(fakeDb(), { agent: 'bot', clients: [client] })
    expect(client.chat.completions.create).not.toBe(original)
    inst.uninstrument()
    expect(client.chat.completions.create).toBe(original)
  })

  it('restores prototype methods by removing the patch', () => {
    class Completions { create() { return Promise.resolve(chatReply('x')) } }
    const completions = new Completions()
    const inst = instrument(fakeDb(), { agent: 'bot', clients: [{ chat: { completions } }] })
    expect(Object.prototype.hasOwnProperty.call(completions, 'create')).toBe(true)
    inst.uninstrument()
    expect(Object.prototype.hasOwnProperty.call(completions, 'create')).toBe(false)
  })

  it('warns when no supported client is given', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    instrument(fakeDb(), { agent: 'bot', clients: [{}] }).uninstrument()
    expect(warn.mock.calls[0][0]).toContain('nothing to record')
    warn.mockRestore()
  })
})

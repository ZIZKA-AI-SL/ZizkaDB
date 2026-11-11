/**
 * One-line auto-capture of LLM calls into ZizkaDB.
 *
 *   import OpenAI from 'openai'
 *   import { ZizkaDB, instrument } from 'zizkadb-sdk'
 *
 *   const db = new ZizkaDB({ apiKey: 'zizkadb_live_...' })
 *   const openai = new OpenAI()
 *   instrument(db, { agent: 'support-bot', clients: [openai] })
 *
 * Every call on the given clients is recorded as causally linked events, so
 * `db.why()` works with no `db.log()` calls:
 *
 *   user_message -> llm_end -> tool_call -> tool_result -> llm_end ...
 *
 * Supported clients (detected by shape, no import of the libraries):
 * - OpenAI: `chat.completions.create` and `responses.create` (also covers
 *   OpenAI-compatible servers such as Ollama, vLLM and LM Studio)
 * - Anthropic: `messages.create`
 *
 * The call returns the client's own promise (so `.withResponse()` and other
 * extras keep working); recording runs in the background and never changes
 * the result or the error. `await handle.flush()` waits for pending writes,
 * for example before a script exits. Streaming calls are recorded without
 * their output.
 */

type AnyRecord = Record<string, any>

/** The part of `ZizkaDB` that instrument() uses. */
export interface InstrumentTarget {
  log(options: {
    agent: string
    event: string
    data: Record<string, unknown>
    parentId?: string
    metadata?: Record<string, unknown>
  }): Promise<{ eventId: string }>
}

export interface InstrumentOptions {
  /** Agent id the events are logged under. */
  agent: string
  /** OpenAI / Anthropic client instances to record. */
  clients: unknown[]
  /**
   * Store prompt/output text and tool arguments (clipped to 4,000 characters).
   * `false` records only models, tool names, token usage and the causal links.
   * Default `true`.
   */
  captureContent?: boolean
}

export interface Instrumentation {
  readonly agent: string
  /** Resolves when every pending event write has finished. */
  flush(): Promise<void>
  /** Restore the original client methods. */
  uninstrument(): void
}

type Shape = 'openai' | 'openai_responses' | 'anthropic'

interface Parsed {
  model: string | null
  text: string | null
  toolCalls: { id: string | null; name: string | null; arguments: unknown }[]
  usage: AnyRecord | null
}

const TEXT_LIMIT = 4000
const TOOL_IDS_KEPT = 1000
const TEXT_BLOCKS = new Set(['text', 'input_text', 'output_text'])

function clip(value: unknown): unknown {
  if (typeof value === 'string' && value.length > TEXT_LIMIT) {
    return `${value.slice(0, TEXT_LIMIT)}… [${value.length - TEXT_LIMIT} more chars]`
  }
  return value
}

function textOf(content: unknown): string | null {
  if (content == null) return null
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return null
  const parts = content
    .filter((b: AnyRecord) => b && TEXT_BLOCKS.has(b.type) && b.text)
    .map((b: AnyRecord) => b.text as string)
  return parts.length ? parts.join('\n') : null
}

function parseArgs(raw: unknown): unknown {
  if (typeof raw !== 'string') return raw
  try {
    return JSON.parse(raw)
  } catch {
    return clip(raw)
  }
}

// ── request parsing ───────────────────────────────────────────────────────

function requestView(shape: Shape, params: AnyRecord): { userText: string | null; results: [string, unknown][] } {
  const results: [string, unknown][] = []
  if (shape === 'openai_responses') {
    const items: AnyRecord[] = typeof params.input === 'string'
      ? [{ role: 'user', content: params.input }]
      : Array.isArray(params.input) ? params.input : []
    for (const item of items) {
      if (item?.type === 'function_call_output' && item.call_id) results.push([item.call_id, clip(item.output)])
    }
    const last = items[items.length - 1]
    return { userText: last?.role === 'user' ? textOf(last.content) : null, results }
  }
  const messages: AnyRecord[] = Array.isArray(params.messages) ? params.messages : []
  for (const msg of messages) {
    if (msg?.role === 'tool' && msg.tool_call_id) results.push([msg.tool_call_id, clip(textOf(msg.content))])
    if (Array.isArray(msg?.content)) {
      for (const block of msg.content) {
        if (block?.type === 'tool_result' && block.tool_use_id) {
          results.push([block.tool_use_id, clip(textOf(block.content) ?? block.content)])
        }
      }
    }
  }
  const last = messages[messages.length - 1]
  let userText: string | null = null
  if (last?.role === 'user') {
    const isToolResult = Array.isArray(last.content) && last.content.some((b: AnyRecord) => b?.type === 'tool_result')
    if (!isToolResult) userText = textOf(last.content)
  }
  return { userText, results }
}

// ── response parsing ──────────────────────────────────────────────────────

function parseOpenAI(resp: AnyRecord, model: string | null): Parsed {
  const message = resp?.choices?.[0]?.message
  const toolCalls = (message?.tool_calls ?? []).map((tc: AnyRecord) => ({
    id: tc.id ?? null, name: tc.function?.name ?? null, arguments: parseArgs(tc.function?.arguments),
  }))
  const u = resp?.usage
  const usage = u ? {
    model: resp.model ?? model,
    input_tokens: u.prompt_tokens ?? 0,
    output_tokens: u.completion_tokens ?? 0,
    cached_tokens: u.prompt_tokens_details?.cached_tokens ?? 0,
  } : null
  return { model: resp?.model ?? model, text: textOf(message?.content), toolCalls, usage }
}

function parseOpenAIResponses(resp: AnyRecord, model: string | null): Parsed {
  const texts: string[] = []
  const toolCalls: Parsed['toolCalls'] = []
  for (const item of resp?.output ?? []) {
    if (item?.type === 'message') {
      const t = textOf(item.content)
      if (t) texts.push(t)
    } else if (item?.type === 'function_call') {
      toolCalls.push({ id: item.call_id ?? null, name: item.name ?? null, arguments: parseArgs(item.arguments) })
    }
  }
  const u = resp?.usage
  const usage = u ? {
    model: resp.model ?? model,
    input_tokens: u.input_tokens ?? 0,
    output_tokens: u.output_tokens ?? 0,
    cached_tokens: u.input_tokens_details?.cached_tokens ?? 0,
    reasoning_tokens: u.output_tokens_details?.reasoning_tokens ?? 0,
  } : null
  return { model: resp?.model ?? model, text: texts.length ? texts.join('\n') : null, toolCalls, usage }
}

function parseAnthropic(resp: AnyRecord, model: string | null): Parsed {
  const texts: string[] = []
  const toolCalls: Parsed['toolCalls'] = []
  for (const block of resp?.content ?? []) {
    if (block?.type === 'text') texts.push(block.text ?? '')
    else if (block?.type === 'tool_use') toolCalls.push({ id: block.id ?? null, name: block.name ?? null, arguments: block.input })
  }
  const u = resp?.usage
  const usage = u ? {
    model: resp.model ?? model,
    input_tokens: u.input_tokens ?? 0,
    output_tokens: u.output_tokens ?? 0,
    cached_tokens: u.cache_read_input_tokens ?? 0,
  } : null
  return { model: resp?.model ?? model, text: texts.length ? texts.join('\n') : null, toolCalls, usage }
}

const PARSERS: Record<Shape, (r: AnyRecord, m: string | null) => Parsed> = {
  openai: parseOpenAI,
  openai_responses: parseOpenAIResponses,
  anthropic: parseAnthropic,
}

// ── recording ─────────────────────────────────────────────────────────────

class Recorder implements Instrumentation {
  private readonly toolEvents = new Map<string, string>()
  private readonly resultsSeen = new Set<string>()
  private tail: Promise<void> = Promise.resolve()
  readonly patches: { owner: AnyRecord; hadOwn: boolean; previous: unknown }[] = []

  constructor(
    private readonly db: InstrumentTarget,
    readonly agent: string,
    private readonly captureContent: boolean,
  ) {}

  private content(value: unknown): unknown {
    return this.captureContent ? clip(value) : null
  }

  private async write(event: string, data: AnyRecord, parentId: string | null): Promise<string | null> {
    try {
      const result = await this.db.log({
        agent: this.agent,
        event,
        data,
        ...(parentId ? { parentId } : {}),
        metadata: { source: 'zizkadb.instrument' },
      })
      return result.eventId
    } catch (err) {
      console.warn(`zizkadb.instrument: could not record ${event}: ${(err as Error)?.message ?? err}`)
      return null
    }
  }

  private remember(map: Map<string, string> | Set<string>, key: string, value?: string): void {
    if (map instanceof Map) map.set(key, value!)
    else map.add(key)
    if (map.size > TOOL_IDS_KEPT) map.delete(map.keys().next().value as string)
  }

  /** Record one call. Writes are chained so a tool result always finds its call. */
  record(provider: string, shape: Shape, params: AnyRecord, response: Promise<unknown>): void {
    // Read the request now: apps often keep appending to the same messages
    // array before the background write runs.
    const { userText, results } = requestView(shape, params)
    const model: string | null = params.model ?? null
    const streamed = Boolean(params.stream)
    const run = async (): Promise<void> => {
      let parent: string | null = null
      for (const [toolId, result] of results) {
        if (this.resultsSeen.has(toolId)) continue
        this.remember(this.resultsSeen, toolId)
        const id = await this.write('tool_result', { tool_call_id: toolId, result: this.content(result) }, this.toolEvents.get(toolId) ?? null)
        parent = id ?? parent
      }
      if (userText != null) {
        parent = (await this.write('user_message', { text: this.content(userText) }, null)) ?? parent
      }
      let resp: unknown
      try {
        resp = await response
      } catch (err) {
        await this.write('llm_error', { provider, model, error: clip(String((err as Error)?.message ?? err)) }, parent)
        return
      }
      let parsed: Parsed | null = null
      if (!streamed) {
        try {
          parsed = PARSERS[shape](resp as AnyRecord, model)
        } catch (err) {
          console.warn(`zizkadb.instrument: could not read the ${shape} response: ${(err as Error)?.message ?? err}`)
        }
      }
      const data: AnyRecord = { provider, model: parsed?.model ?? model }
      if (streamed) {
        data.streamed = true
        data.note = 'streaming response; output not captured'
      }
      if (parsed) {
        data.output = this.content(parsed.text)
        if (parsed.toolCalls.length) data.tool_calls = parsed.toolCalls.map((c) => c.name)
        if (parsed.usage) data.token_usage = parsed.usage
      }
      const llmId = await this.write('llm_end', data, parent)
      for (const call of parsed?.toolCalls ?? []) {
        const id = await this.write('tool_call', { tool: call.name, tool_call_id: call.id, arguments: this.content(call.arguments) }, llmId)
        if (call.id && id) this.remember(this.toolEvents, call.id, id)
      }
    }
    this.tail = this.tail.then(run, run).catch(() => {})
  }

  wrap(owner: AnyRecord, provider: string, shape: Shape): void {
    const original = owner.create
    if (typeof original !== 'function') return
    const hadOwn = Object.prototype.hasOwnProperty.call(owner, 'create')
    this.patches.push({ owner, hadOwn, previous: hadOwn ? original : undefined })
    const recorder = this
    owner.create = function (this: unknown, params: AnyRecord, ...rest: unknown[]) {
      const result = original.call(this ?? owner, params, ...rest)
      try {
        recorder.record(provider, shape, params ?? {}, Promise.resolve(result))
      } catch {
        // recording must never affect the call
      }
      return result
    }
  }

  flush(): Promise<void> {
    return this.tail
  }

  uninstrument(): void {
    for (const { owner, hadOwn, previous } of this.patches.reverse()) {
      if (hadOwn) owner.create = previous
      else delete owner.create
    }
    this.patches.length = 0
  }
}

/**
 * Record every chat / message call on the given OpenAI and Anthropic clients
 * as causally linked events. Returns a handle with `flush()` and
 * `uninstrument()`.
 */
export function instrument(db: InstrumentTarget, options: InstrumentOptions): Instrumentation {
  const recorder = new Recorder(db, options.agent, options.captureContent !== false)
  for (const client of options.clients ?? []) {
    const c = client as AnyRecord
    if (!c || typeof c !== 'object') continue
    if (c.chat?.completions) recorder.wrap(c.chat.completions, 'openai', 'openai')
    if (c.responses) recorder.wrap(c.responses, 'openai', 'openai_responses')
    if (c.messages && !c.chat) recorder.wrap(c.messages, 'anthropic', 'anthropic')
  }
  if (!recorder.patches.length) {
    console.warn('zizkadb.instrument: no OpenAI or Anthropic client found in `clients`; nothing to record')
  }
  return recorder
}

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
 *   OpenAI-compatible APIs: DeepSeek, GLM, Kimi, Ollama, vLLM, LM Studio...)
 * - Anthropic: `messages.create`
 * - Mistral (`@mistralai/mistralai`): `chat.complete`, `chat.stream`
 * - Cohere (`cohere-ai` CohereClientV2): `chat`, `chatStream`
 * - AWS Bedrock (`@aws-sdk/client-bedrock-runtime`): `send()` of
 *   ConverseCommand / ConverseStreamCommand
 *
 * The call returns the client's own promise (so `.withResponse()` and other
 * extras keep working); recording runs in the background and never changes
 * the result or the error. `await handle.flush()` waits for pending writes,
 * for example before a script exits.
 *
 * Streaming (`stream: true`): you iterate the client's stream as usual; chunks
 * are read as they pass and the events are written when the stream ends, with
 * the full output, tool calls and token usage (OpenAI chat only sends usage
 * with `stream_options: { include_usage: true }`). A stream you stop early is
 * recorded with `truncated: true`; one that fails mid-way as `llm_error` with
 * the partial output. The `.stream()` helpers (`chat.completions.stream`,
 * `responses.stream`, Anthropic `messages.stream`) call `create()` and are
 * recorded through it.
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
  /**
   * OpenAI / Anthropic clients to record: instances (`new OpenAI()`), or the
   * client classes themselves (`OpenAI`, `Anthropic`) to record every instance,
   * including ones your code creates later.
   */
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

type Shape = 'openai' | 'openai_responses' | 'anthropic' | 'ai_sdk' | 'mistral' | 'cohere' | 'bedrock'

interface Parsed {
  model: string | null
  text: string | null
  toolCalls: { id: string | null; name: string | null; arguments: unknown }[]
  usage: AnyRecord | null
  /** thinking text: DeepSeek / GLM / Kimi `reasoning_content`, AI SDK reasoning parts */
  reasoning?: string | null
}

const TEXT_LIMIT = 4000
const TOOL_IDS_KEPT = 1000
const TEXT_BLOCKS = new Set(['text', 'input_text', 'output_text'])

/** First defined field among snake_case / camelCase spellings (native SDKs use camelCase). */
function pick(obj: AnyRecord | null | undefined, ...names: string[]): any {
  if (!obj) return undefined
  for (const n of names) if (obj[n] !== undefined && obj[n] !== null) return obj[n]
  return undefined
}

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

/** Text of an AI SDK tool result, across spec versions (`output` in v5+, `result` in v4). */
function aiSdkToolOutput(part: AnyRecord): unknown {
  const out = part.output ?? part.result
  if (out && typeof out === 'object' && 'value' in out) return out.value
  return out
}

function requestView(shape: Shape, params: AnyRecord): { userText: string | null; results: [string, unknown][] } {
  const results: [string, unknown][] = []
  if (shape === 'ai_sdk') {
    const prompt: AnyRecord[] = Array.isArray(params.prompt) ? params.prompt : []
    for (const msg of prompt) {
      if (msg?.role !== 'tool' || !Array.isArray(msg.content)) continue
      for (const part of msg.content) {
        if (part?.type === 'tool-result' && part.toolCallId) results.push([part.toolCallId, clip(aiSdkToolOutput(part))])
      }
    }
    const last = prompt[prompt.length - 1]
    return { userText: last?.role === 'user' ? textOf(last.content) : null, results }
  }
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
  if (shape === 'bedrock') {
    const messages: AnyRecord[] = Array.isArray(params.messages) ? params.messages : []
    for (const msg of messages) {
      for (const block of msg?.content ?? []) {
        const tr = block?.toolResult
        if (tr?.toolUseId) {
          const texts = (tr.content ?? []).filter((p: AnyRecord) => p?.text).map((p: AnyRecord) => p.text)
          results.push([tr.toolUseId, texts.length ? clip(texts.join('\n')) : (tr.content ?? []).map((p: AnyRecord) => p?.json)])
        }
      }
    }
    const last = messages[messages.length - 1]
    let userText: string | null = null
    if (last?.role === 'user' && !(last.content ?? []).some((b: AnyRecord) => b?.toolResult)) {
      const texts = (last.content ?? []).filter((b: AnyRecord) => b?.text).map((b: AnyRecord) => b.text)
      userText = texts.length ? texts.join('\n') : null
    }
    return { userText, results }
  }
  const messages: AnyRecord[] = Array.isArray(params.messages) ? params.messages : []
  for (const msg of messages) {
    const toolCallId = pick(msg, 'tool_call_id', 'toolCallId')
    if (msg?.role === 'tool' && toolCallId) {
      const text = textOf(msg.content)
      results.push([toolCallId, text !== null ? clip(text) : msg.content])
    }
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

/** token_usage from an OpenAI-shaped usage block, including the variants
 *  OpenAI-compatible providers use (DeepSeek cache hits, Kimi cached_tokens). */
function openaiUsage(u: AnyRecord, model: string | null): AnyRecord {
  const out: AnyRecord = {
    model,
    input_tokens: pick(u, 'prompt_tokens', 'promptTokens') ?? 0,
    output_tokens: pick(u, 'completion_tokens', 'completionTokens') ?? 0,
    cached_tokens: pick(u, 'prompt_tokens_details', 'promptTokensDetails')?.cached_tokens || u.prompt_cache_hit_tokens || u.cached_tokens || 0,
  }
  const reasoning = pick(u, 'completion_tokens_details', 'completionTokensDetails')?.reasoning_tokens
  if (reasoning) out.reasoning_tokens = reasoning
  return out
}

function parseOpenAI(resp: AnyRecord, model: string | null): Parsed {
  const message = resp?.choices?.[0]?.message
  const toolCalls = (pick(message, 'tool_calls', 'toolCalls') ?? []).map((tc: AnyRecord) => ({
    id: tc.id ?? null, name: tc.function?.name ?? null, arguments: parseArgs(tc.function?.arguments),
  }))
  const usage = resp?.usage ? openaiUsage(resp.usage, resp.model ?? model) : null
  return {
    model: resp?.model ?? model, text: textOf(message?.content), toolCalls, usage,
    reasoning: pick(message, 'reasoning_content', 'reasoningContent', 'reasoning') ?? null,
  }
}

function parseCohere(resp: AnyRecord, model: string | null): Parsed {
  const message = resp?.message
  const texts = (Array.isArray(message?.content) ? message.content : [])
    .filter((b: AnyRecord) => b?.type === 'text' && b.text).map((b: AnyRecord) => b.text as string)
  const toolCalls = (pick(message, 'tool_calls', 'toolCalls') ?? []).map((tc: AnyRecord) => ({
    id: tc.id ?? null, name: tc.function?.name ?? null, arguments: parseArgs(tc.function?.arguments),
  }))
  const tokens = pick(resp?.usage, 'tokens', 'billed_units', 'billedUnits')
  const usage = tokens ? {
    model,
    input_tokens: Math.round(pick(tokens, 'input_tokens', 'inputTokens') ?? 0),
    output_tokens: Math.round(pick(tokens, 'output_tokens', 'outputTokens') ?? 0),
    cached_tokens: 0,
  } : null
  return { model, text: texts.length ? texts.join('') : null, toolCalls, usage, reasoning: pick(message, 'tool_plan', 'toolPlan') ?? null }
}

function bedrockUsage(u: AnyRecord | undefined, model: string | null): AnyRecord | null {
  if (!u) return null
  return { model, input_tokens: u.inputTokens ?? 0, output_tokens: u.outputTokens ?? 0, cached_tokens: u.cacheReadInputTokens ?? 0 }
}

function parseBedrock(resp: AnyRecord, model: string | null): Parsed {
  const texts: string[] = []
  const reasoning: string[] = []
  const toolCalls: Parsed['toolCalls'] = []
  for (const block of resp?.output?.message?.content ?? []) {
    if (block?.text) texts.push(block.text)
    else if (block?.toolUse) toolCalls.push({ id: block.toolUse.toolUseId ?? null, name: block.toolUse.name ?? null, arguments: block.toolUse.input })
    else if (block?.reasoningContent?.reasoningText?.text) reasoning.push(block.reasoningContent.reasoningText.text)
  }
  return { model, text: texts.length ? texts.join('') : null, toolCalls, usage: bedrockUsage(resp?.usage, model), reasoning: reasoning.length ? reasoning.join('') : null }
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

/** token_usage from AI SDK usage: v3 nests totals (`inputTokens.total`), v2 uses numbers, v1 `promptTokens`. */
function aiSdkUsage(u: AnyRecord | undefined, model: string | null): AnyRecord | null {
  if (!u) return null
  const num = (v: unknown) => (typeof v === 'number' ? v : 0)
  const nested = typeof u.inputTokens === 'object' && u.inputTokens !== null
  const out: AnyRecord = {
    model,
    input_tokens: nested ? num(u.inputTokens.total) : num(u.inputTokens ?? u.promptTokens),
    output_tokens: typeof u.outputTokens === 'object' && u.outputTokens !== null ? num(u.outputTokens.total) : num(u.outputTokens ?? u.completionTokens),
    cached_tokens: nested ? num(u.inputTokens.cacheRead) : num(u.cachedInputTokens),
  }
  const reasoning = typeof u.outputTokens === 'object' && u.outputTokens !== null ? u.outputTokens.reasoning : u.reasoningTokens
  if (reasoning) out.reasoning_tokens = reasoning
  return out
}

function parseAiSdk(result: AnyRecord, model: string | null): Parsed {
  const texts: string[] = []
  const reasoning: string[] = []
  const toolCalls: Parsed['toolCalls'] = []
  if (Array.isArray(result?.content)) {
    for (const part of result.content) {
      if (part?.type === 'text' && part.text) texts.push(part.text)
      else if (part?.type === 'reasoning' && part.text) reasoning.push(part.text)
      else if (part?.type === 'tool-call') {
        toolCalls.push({ id: part.toolCallId ?? null, name: part.toolName ?? null, arguments: parseArgs(part.input ?? part.args) })
      }
    }
  } else {
    // spec v1 (AI SDK 4): text / toolCalls / reasoning on the result
    if (result?.text) texts.push(result.text)
    if (typeof result?.reasoning === 'string') reasoning.push(result.reasoning)
    for (const tc of result?.toolCalls ?? []) toolCalls.push({ id: tc.toolCallId ?? null, name: tc.toolName ?? null, arguments: parseArgs(tc.args) })
  }
  const m = result?.response?.modelId ?? model
  return { model: m, text: texts.length ? texts.join('') : null, toolCalls, usage: aiSdkUsage(result?.usage, m), reasoning: reasoning.length ? reasoning.join('') : null }
}

const PARSERS: Record<Shape, (r: AnyRecord, m: string | null) => Parsed> = {
  openai: parseOpenAI,
  openai_responses: parseOpenAIResponses,
  anthropic: parseAnthropic,
  ai_sdk: parseAiSdk,
  mistral: parseOpenAI,
  cohere: parseCohere,
  bedrock: parseBedrock,
}

// ── streaming ─────────────────────────────────────────────────────────────

/** Rebuilds the parsed result of a streamed call from its chunks/events. */
class StreamAccumulator {
  private readonly texts: string[] = []
  private readonly tools = new Map<number, { id: string | null; name: string | null; args: string }>()
  private usage: AnyRecord | null = null
  private final: Parsed | null = null
  private readonly reasoning: string[] = []
  private readonly wholeCalls: Parsed['toolCalls'] = []

  constructor(private readonly shape: Shape, private model: string | null) {}

  /** Set while streaming, for parts that carry no model id. */
  get modelId(): string | null {
    return this.model
  }

  feed(chunk: unknown): void {
    try {
      const c = chunk as AnyRecord
      if (this.shape === 'openai') this.feedOpenAI(c)
      else if (this.shape === 'openai_responses') this.feedResponses(c)
      else if (this.shape === 'ai_sdk') this.feedAiSdk(c)
      else if (this.shape === 'mistral') this.feedOpenAI(c?.data ?? c)
      else if (this.shape === 'cohere') this.feedCohere(c)
      else if (this.shape === 'bedrock') this.feedBedrock(c)
      else this.feedAnthropic(c)
    } catch {
      // a chunk we cannot read is skipped; the caller's stream is unaffected
    }
  }

  private slot(index: number) {
    let slot = this.tools.get(index)
    if (!slot) {
      slot = { id: null, name: null, args: '' }
      this.tools.set(index, slot)
    }
    return slot
  }

  private feedOpenAI(c: AnyRecord): void {
    this.model = c?.model ?? this.model
    for (const choice of c?.choices ?? []) {
      const delta = choice?.delta ?? {}
      if (delta.content) this.texts.push(delta.content)
      if (delta.reasoning_content || delta.reasoning) this.reasoning.push(delta.reasoning_content || delta.reasoning)
      for (const tc of pick(delta, 'tool_calls', 'toolCalls') ?? []) {
        const args = tc.function?.arguments
        if (args && typeof args === 'object') {
          // Mistral sends whole arguments
          this.wholeCalls.push({ id: tc.id ?? null, name: tc.function?.name ?? null, arguments: args })
          continue
        }
        const slot = this.slot(tc.index ?? this.tools.size)
        slot.id = tc.id ?? slot.id
        slot.name = tc.function?.name ?? slot.name
        slot.args += args ?? ''
      }
    }
    // only sent with stream_options: { include_usage: true }
    if (c?.usage) this.usage = openaiUsage(c.usage, this.model)
  }

  /** AI SDK stream parts (spec v1-v3). */
  private feedAiSdk(p: AnyRecord): void {
    if (p?.type === 'text-delta') this.texts.push(p.delta ?? p.textDelta ?? '')
    else if (p?.type === 'reasoning-delta' || p?.type === 'reasoning') this.reasoning.push(p.delta ?? p.textDelta ?? p.text ?? '')
    else if (p?.type === 'tool-call') {
      this.wholeCalls.push({ id: p.toolCallId ?? null, name: p.toolName ?? null, arguments: parseArgs(p.input ?? p.args) })
    } else if (p?.type === 'response-metadata' && p.modelId) this.model = p.modelId
    else if (p?.type === 'finish') this.usage = aiSdkUsage(p.usage, this.model)
  }

  private feedCohere(e: AnyRecord): void {
    const message = e?.delta?.message
    if (e?.type === 'content-delta' && message?.content?.text) this.texts.push(message.content.text)
    else if (e?.type === 'tool-plan-delta' && pick(message, 'tool_plan', 'toolPlan')) this.reasoning.push(pick(message, 'tool_plan', 'toolPlan'))
    else if (e?.type === 'tool-call-start' || e?.type === 'tool-call-delta') {
      const tc = pick(message, 'tool_calls', 'toolCalls') ?? {}
      const slot = this.slot(e.index ?? this.tools.size)
      slot.id = tc.id ?? slot.id
      slot.name = tc.function?.name ?? slot.name
      slot.args += tc.function?.arguments ?? ''
    } else if (e?.type === 'message-end') {
      const tokens = pick(e.delta?.usage, 'tokens', 'billed_units', 'billedUnits')
      if (tokens) {
        this.usage = {
          model: this.model,
          input_tokens: Math.round(pick(tokens, 'input_tokens', 'inputTokens') ?? 0),
          output_tokens: Math.round(pick(tokens, 'output_tokens', 'outputTokens') ?? 0),
          cached_tokens: 0,
        }
      }
    }
  }

  private feedBedrock(e: AnyRecord): void {
    if (e?.contentBlockStart?.start?.toolUse) {
      const tu = e.contentBlockStart.start.toolUse
      this.tools.set(e.contentBlockStart.contentBlockIndex ?? this.tools.size, { id: tu.toolUseId ?? null, name: tu.name ?? null, args: '' })
    } else if (e?.contentBlockDelta) {
      const d = e.contentBlockDelta.delta ?? {}
      if (d.text) this.texts.push(d.text)
      else if (d.toolUse && this.tools.has(e.contentBlockDelta.contentBlockIndex)) this.tools.get(e.contentBlockDelta.contentBlockIndex)!.args += d.toolUse.input ?? ''
      else if (d.reasoningContent?.text) this.reasoning.push(d.reasoningContent.text)
    } else if (e?.metadata?.usage) this.usage = bedrockUsage(e.metadata.usage, this.model)
  }

  private feedResponses(e: AnyRecord): void {
    if (e?.type === 'response.output_text.delta' && e.delta) this.texts.push(e.delta)
    else if ((e?.type === 'response.completed' || e?.type === 'response.incomplete') && e.response) {
      this.final = parseOpenAIResponses(e.response, this.model)
    }
  }

  private feedAnthropic(e: AnyRecord): void {
    if (e?.type === 'message_start') {
      this.model = e.message?.model ?? this.model
      if (e.message?.usage) this.usage = parseAnthropic({ model: this.model, usage: e.message.usage }, this.model).usage
    } else if (e?.type === 'content_block_start' && e.content_block?.type === 'tool_use') {
      this.tools.set(e.index, { id: e.content_block.id ?? null, name: e.content_block.name ?? null, args: '' })
    } else if (e?.type === 'content_block_delta') {
      if (e.delta?.type === 'text_delta') this.texts.push(e.delta.text ?? '')
      else if (e.delta?.type === 'input_json_delta' && this.tools.has(e.index)) this.tools.get(e.index)!.args += e.delta.partial_json ?? ''
    } else if (e?.type === 'message_delta' && e.usage && this.usage) {
      this.usage.output_tokens = e.usage.output_tokens ?? 0
    }
  }

  result(): Parsed {
    if (this.final) return this.final
    const toolCalls = [...this.tools.entries()]
      .sort(([a], [b]) => a - b)
      .map(([, t]) => ({ id: t.id, name: t.name, arguments: t.args ? parseArgs(t.args) : {} }))
      .concat(this.wholeCalls)
    if (this.usage) this.usage.model = this.model ?? this.usage.model
    return {
      model: this.model, text: this.texts.length ? this.texts.join('') : null, toolCalls, usage: this.usage,
      reasoning: this.reasoning.length ? this.reasoning.join('') : null,
    }
  }
}

// ── recording ─────────────────────────────────────────────────────────────

/** Set while a `capture()` block runs: other recorders stay quiet so a test sends nothing to ZizkaDB. */
let capturing = 0
let captureRecorder: Recorder | null = null

/** @internal Used by `capture()` in ./testing. */
export function _setCapturing(on: boolean, recorder: Recorder | null = null): void {
  capturing += on ? 1 : -1
  captureRecorder = on ? recorder : capturing > 0 ? captureRecorder : null
}

/** @internal How a wrapped method returns its stream. */
interface WrapOptions {
  /** The method always streams (`chat.stream`, `chatStream`), whatever its params say. */
  alwaysStream?: boolean
  /** The stream is a property of the response (`response.stream`). */
  streamProp?: string
}

export class Recorder implements Instrumentation {
  private readonly toolEvents = new Map<string, string>()
  private readonly resultsSeen = new Set<string>()
  private tail: Promise<void> = Promise.resolve()
  readonly patches: { owner: AnyRecord; method: string; hadOwn: boolean; previous: unknown }[] = []

  constructor(
    private readonly db: InstrumentTarget,
    readonly agent: string,
    private readonly captureContent: boolean,
    /** @internal true for the in-memory recorder of `capture()` */
    readonly forCapture = false,
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

  /** Writes `llm_end` and one `tool_call` per tool the model asked for. */
  private async after(provider: string, model: string | null, parsed: Parsed | null, parent: string | null, extra: AnyRecord = {}): Promise<void> {
    const data: AnyRecord = { provider, model: parsed?.model ?? model, ...extra }
    if (parsed) {
      data.output = this.content(parsed.text)
      if (parsed.reasoning) data.reasoning = this.content(parsed.reasoning)
      if (parsed.toolCalls.length) data.tool_calls = parsed.toolCalls.map((c) => c.name)
      if (parsed.usage) data.token_usage = parsed.usage
    }
    const llmId = await this.write('llm_end', data, parent)
    for (const call of parsed?.toolCalls ?? []) {
      const id = await this.write('tool_call', { tool: call.name, tool_call_id: call.id, arguments: this.content(call.arguments) }, llmId)
      if (call.id && id) this.remember(this.toolEvents, call.id, id)
    }
  }

  /** Queue a write after everything recorded so far, so a tool result always finds its call. */
  private enqueue(task: () => Promise<void>): void {
    this.tail = this.tail.then(task, task).catch(() => {})
  }

  /**
   * @internal Queue the events that come before a call (tool results, the new
   * user message). Returns a getter for the event the call's answer links to.
   */
  begin(shape: Shape, params: AnyRecord): () => string | null {
    // Read the request now: apps often keep appending to the same messages
    // array before the background write runs.
    const { userText, results } = requestView(shape, params)
    let parent: string | null = null
    this.enqueue(async () => {
      for (const [toolId, res] of results) {
        if (this.resultsSeen.has(toolId)) continue
        this.remember(this.resultsSeen, toolId)
        const id = await this.write('tool_result', { tool_call_id: toolId, result: this.content(res) }, this.toolEvents.get(toolId) ?? null)
        parent = id ?? parent
      }
      if (userText != null) {
        parent = (await this.write('user_message', { text: this.content(userText) }, null)) ?? parent
      }
    })
    return () => parent
  }

  /** @internal Queue `llm_end` (+ tool calls) after everything before it. */
  finish(provider: string, model: string | null, parsed: Parsed | null, parent: () => string | null, extra: AnyRecord = {}): void {
    this.enqueue(() => this.after(provider, model, parsed, parent(), extra))
  }

  /** @internal Queue `llm_error`. */
  fail(provider: string, model: string | null, err: unknown, parent: () => string | null, partial?: Parsed): void {
    const data: AnyRecord = { provider, model: partial?.model ?? model, error: clip(String((err as Error)?.message ?? err)) }
    if (partial) {
      data.streamed = true
      data.partial_output = this.content(partial.text)
    }
    this.enqueue(() => this.write('llm_error', data, parent()).then(() => {}))
  }

  /** Record one call. `result` is what the client returned, untouched. */
  record(provider: string, shape: Shape, params: AnyRecord, result: unknown, opts: WrapOptions = {}): void {
    if (capturing > 0 && !this.forCapture) return
    const model: string | null = params.model ?? null
    const parent = this.begin(shape, params)
    const onError = (err: unknown) => this.fail(provider, model, err, parent)

    // Handlers go straight on the client's own promise, at call time, so they
    // run before the caller's `await` resumes: the stream is wrapped before it
    // is read, and this call's events are queued before the next call's.
    const thenable = (typeof (result as AnyRecord)?.then === 'function' ? result : Promise.resolve(result)) as {
      then: (ok: (v: unknown) => void, bad: (e: unknown) => void) => unknown
    }

    if (opts.alwaysStream || params.stream) {
      thenable.then((value) => {
        try {
          // Bedrock ConverseStream: the events are on response.stream
          const stream = opts.streamProp ? (value as AnyRecord)?.[opts.streamProp] : value
          this.wrapStream(stream as AnyRecord, shape, model, (outcome, acc, err) => {
            if (outcome === 'error') this.fail(provider, model, err, parent, acc.result())
            else this.finish(provider, model, acc.result(), parent, outcome === 'truncated' ? { streamed: true, truncated: true } : { streamed: true })
          })
        } catch (err) {
          console.warn(`zizkadb.instrument: could not wrap the stream: ${(err as Error)?.message ?? err}`)
        }
      }, onError)
      return
    }

    thenable.then((resp) => {
      let parsed: Parsed | null = null
      try {
        parsed = PARSERS[shape](resp as AnyRecord, model)
      } catch (err) {
        console.warn(`zizkadb.instrument: could not read the ${shape} response: ${(err as Error)?.message ?? err}`)
      }
      this.finish(provider, model, parsed, parent)
    }, onError)
  }

  /**
   * Swap the stream's async iterator for one that passes every chunk through
   * unchanged and reports how the stream ended: read to the end, stopped
   * early (`break`, `return`, abort), or failed.
   */
  private wrapStream(
    stream: AnyRecord,
    shape: Shape,
    model: string | null,
    done: (outcome: 'complete' | 'truncated' | 'error', acc: StreamAccumulator, err?: unknown) => void,
  ): void {
    const target = stream as Record<PropertyKey, any>
    const original = target?.[Symbol.asyncIterator]
    if (typeof original !== 'function') return
    const acc = new StreamAccumulator(shape, model)
    let finished = false
    const finish = (outcome: 'complete' | 'truncated' | 'error', err?: unknown) => {
      if (finished) return
      finished = true
      done(outcome, acc, err)
    }
    target[Symbol.asyncIterator] = async function* (this: unknown) {
      const it = original.call(stream)
      let complete = false
      try {
        while (true) {
          let step: IteratorResult<unknown>
          try {
            step = await it.next()
          } catch (err) {
            finish('error', err)
            throw err
          }
          if (step.done) {
            complete = true
            finish('complete')
            return
          }
          acc.feed(step.value)
          yield step.value
        }
      } finally {
        if (!complete && !finished) {
          finish('truncated')
          await it.return?.()
        }
      }
    }
  }

  wrap(owner: AnyRecord, provider: string, shape: Shape, method = 'create', opts: WrapOptions = {}): void {
    const original = owner[method]
    if (typeof original !== 'function') return
    const hadOwn = Object.prototype.hasOwnProperty.call(owner, method)
    this.patches.push({ owner, method, hadOwn, previous: hadOwn ? original : undefined })
    const recorder = this
    owner[method] = function (this: unknown, params: AnyRecord, ...rest: unknown[]) {
      const result = original.call(this ?? owner, params, ...rest)
      try {
        recorder.record(provider, shape, params ?? {}, result, opts)
      } catch {
        // recording must never affect the call
      }
      return result
    }
  }

  /** AWS Bedrock: `client.send(command)`; only Converse / ConverseStream commands are recorded. */
  wrapBedrock(client: AnyRecord): void {
    const original = client.send
    if (typeof original !== 'function') return
    const hadOwn = Object.prototype.hasOwnProperty.call(client, 'send')
    this.patches.push({ owner: client, method: 'send', hadOwn, previous: hadOwn ? original : undefined })
    const recorder = this
    client.send = function (this: unknown, command: AnyRecord, ...rest: unknown[]) {
      const result = original.call(this ?? client, command, ...rest)
      const name = command?.constructor?.name
      if (name === 'ConverseCommand' || name === 'ConverseStreamCommand') {
        try {
          const input = command.input ?? {}
          recorder.record('bedrock', 'bedrock', { model: input.modelId ?? null, messages: input.messages }, result,
            name === 'ConverseStreamCommand' ? { alwaysStream: true, streamProp: 'stream' } : {})
        } catch {
          // recording must never affect the call
        }
      }
      return result
    }
  }

  flush(): Promise<void> {
    return this.tail
  }

  uninstrument(): void {
    for (const { owner, method, hadOwn, previous } of this.patches.reverse()) {
      if (hadOwn) owner[method] = previous
      else delete owner[method]
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
  return _instrument(new Recorder(db, options.agent, options.captureContent !== false), options.clients)
}

/** @internal Shared by instrument() and capture(). */
export function _instrument(recorder: Recorder, clients: unknown[]): Instrumentation {
  for (const client of clients ?? []) {
    const c = client as AnyRecord
    if (typeof client === 'function') {
      // A client class: patch its resource prototypes, so every instance is recorded.
      const k = client as unknown as AnyRecord
      if (k.Chat?.Completions?.prototype) recorder.wrap(k.Chat.Completions.prototype, 'openai', 'openai')
      if (k.Responses?.prototype?.create) recorder.wrap(k.Responses.prototype, 'openai', 'openai_responses')
      if (k.Messages?.prototype?.create && !k.Chat) recorder.wrap(k.Messages.prototype, 'anthropic', 'anthropic')
      continue
    }
    if (!c || typeof c !== 'object') continue
    if (c.chat?.completions) recorder.wrap(c.chat.completions, 'openai', 'openai')
    if (c.responses) recorder.wrap(c.responses, 'openai', 'openai_responses')
    if (c.messages && !c.chat) recorder.wrap(c.messages, 'anthropic', 'anthropic')
    // Mistral: client.chat.complete / client.chat.stream
    if (c.chat && !c.chat.completions && typeof c.chat.complete === 'function') {
      recorder.wrap(c.chat, 'mistral', 'mistral', 'complete')
      recorder.wrap(c.chat, 'mistral', 'mistral', 'stream', { alwaysStream: true })
    }
    // Cohere v2: client.chat / client.chatStream
    if (typeof c.chatStream === 'function' && typeof c.chat === 'function') {
      recorder.wrap(c, 'cohere', 'cohere', 'chat')
      recorder.wrap(c, 'cohere', 'cohere', 'chatStream', { alwaysStream: true })
    }
    // AWS Bedrock runtime: client.send(new ConverseCommand(...))
    if (typeof c.send === 'function' && c.constructor?.name === 'BedrockRuntimeClient') recorder.wrapBedrock(c)
  }
  if (!recorder.patches.length) {
    console.warn('zizkadb.instrument: no supported client found in `clients`; nothing to record')
  }
  return recorder
}


// ── Vercel AI SDK ─────────────────────────────────────────────────────────

export interface MiddlewareOptions {
  agent: string
  /** Same as instrument(): `false` keeps prompt and output text out. */
  captureContent?: boolean
}

/**
 * Language-model middleware for the Vercel AI SDK, so every model you wrap is
 * recorded, whatever its provider (OpenAI, Anthropic, Google, DeepSeek,
 * Mistral, Groq, Ollama, ...):
 *
 *   import { wrapLanguageModel, generateText } from 'ai'
 *   const model = wrapLanguageModel({ model: openai('gpt-4o'), middleware: zizkadbMiddleware(db, { agent: 'bot' }) })
 *
 * `generateText` / `streamText` then record `user_message` -> `llm_end` ->
 * `tool_call` -> `tool_result` like `instrument()`, including multi-step tool
 * loops. Works with AI SDK 4, 5 and 6 (model spec v1-v3). Inside `capture()`
 * the events go to the test's in-memory run instead.
 */
export function zizkadbMiddleware(db: InstrumentTarget, options: MiddlewareOptions): AnyRecord {
  const own = new Recorder(db, options.agent, options.captureContent !== false)
  const recorder = (): Recorder | null => (capturing > 0 ? captureRecorder : own)
  const modelId = (model: AnyRecord | undefined, params: AnyRecord): string | null => model?.modelId ?? params?.modelId ?? null
  const providerOf = (model: AnyRecord | undefined): string => model?.provider ?? 'ai-sdk'

  return {
    specificationVersion: 'v3',
    middlewareVersion: 'v2',

    async wrapGenerate({ doGenerate, params, model }: { doGenerate: () => Promise<AnyRecord>; params: AnyRecord; model?: AnyRecord }) {
      const rec = recorder()
      if (!rec) return doGenerate()
      const id = modelId(model, params)
      const parent = rec.begin('ai_sdk', params)
      let result: AnyRecord
      try {
        result = await doGenerate()
      } catch (err) {
        rec.fail(providerOf(model), id, err, parent)
        throw err
      }
      let parsed: Parsed | null = null
      try {
        parsed = parseAiSdk(result, id)
      } catch (err) {
        console.warn(`zizkadb.instrument: could not read the AI SDK result: ${(err as Error)?.message ?? err}`)
      }
      rec.finish(providerOf(model), id, parsed, parent)
      return result
    },

    async wrapStream({ doStream, params, model }: { doStream: () => Promise<AnyRecord>; params: AnyRecord; model?: AnyRecord }) {
      const rec = recorder()
      if (!rec) return doStream()
      const id = modelId(model, params)
      const provider = providerOf(model)
      const parent = rec.begin('ai_sdk', params)
      let res: AnyRecord
      try {
        res = await doStream()
      } catch (err) {
        rec.fail(provider, id, err, parent)
        throw err
      }
      const acc = new StreamAccumulator('ai_sdk', id)
      let done = false
      let streamError: unknown
      const end = (outcome: 'complete' | 'truncated' | 'error', err?: unknown) => {
        if (done) return
        done = true
        if (outcome === 'error') rec.fail(provider, id, err, parent, acc.result())
        else rec.finish(provider, id, acc.result(), parent, outcome === 'truncated' ? { streamed: true, truncated: true } : { streamed: true })
      }
      const reader = (res.stream as ReadableStream<unknown>).getReader()
      const stream = new ReadableStream<unknown>({
        async pull(controller) {
          let step: ReadableStreamReadResult<unknown>
          try {
            step = await reader.read()
          } catch (err) {
            end('error', err)
            controller.error(err)
            return
          }
          if (step.done) {
            end(streamError !== undefined ? 'error' : 'complete', streamError)
            controller.close()
            return
          }
          const part = step.value as AnyRecord
          if (part?.type === 'error') streamError = part.error
          acc.feed(part)
          controller.enqueue(part)
        },
        cancel(reason) {
          end('truncated')
          return reader.cancel(reason)
        },
      })
      return { ...res, stream }
    },
  }
}

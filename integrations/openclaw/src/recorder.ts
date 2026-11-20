/**
 * Turns OpenClaw hook events into linked ZizkaDB events.
 *
 * One OpenClaw turn (a `runId`) becomes one causal chain:
 *
 *   user_message → tool_call → tool_result → … → llm_end → assistant_response
 *
 * The user message comes from `message_received` (chat channels) or, for
 * turns started another way (CLI, API), from the prompt in `llm_input`.
 *
 * Each event's parent is the previous event of the same turn, so
 * `db.why(<reply>)` walks back to the message that started it. Writes run in
 * the background, one turn at a time in order; a hook handler never waits on
 * the network, and a failed write never reaches the agent.
 */

export interface LogTarget {
  log(options: {
    agent: string
    event: string
    data: Record<string, unknown>
    parentId?: string
    sessionId?: string
    metadata?: Record<string, unknown>
  }): Promise<{ eventId: string }>
}

export interface RecorderOptions {
  /** ZizkaDB agent name for everything this gateway records. */
  agent: string
  /** false: keep prompts, replies, tool arguments and results out of the record. */
  captureContent: boolean
  warn: (message: string) => void
}

/** Subset of OpenClaw's hook context fields the recorder reads. */
export interface HookContext {
  runId?: string
  sessionKey?: string
  sessionId?: string
  agentId?: string
  channelId?: string
  channel?: string
}

export interface Usage {
  input?: number
  output?: number
  cacheRead?: number
  cacheWrite?: number
  total?: number
}

type Data = Record<string, unknown>

interface Turn {
  sessionId?: string
  /** A user_message was queued for this turn. */
  hasUser: boolean
  /** Event id the next event of this turn links to (resolves after the previous write). */
  tail: Promise<string | null>
}

const MAX_TURNS = 256
const MAX_TEXT = 8_000

/** Cut long values so one huge tool result can't bloat the record. */
export function clip(value: unknown): unknown {
  if (value == null) return value
  if (typeof value === 'string') return value.length > MAX_TEXT ? `${value.slice(0, MAX_TEXT)}… [truncated]` : value
  if (typeof value === 'number' || typeof value === 'boolean') return value
  let text: string
  try {
    text = JSON.stringify(value)
  } catch {
    return String(value).slice(0, MAX_TEXT)
  }
  if (text === undefined) return null
  return text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}… [truncated]` : JSON.parse(text)
}

export class Recorder {
  private readonly turns = new Map<string, Turn>()
  /** Latest turn per conversation: outgoing-message hooks carry no runId yet. */
  private readonly latestTurn = new Map<string, string>()
  private readonly pending = new Set<Promise<unknown>>()

  constructor(
    private readonly db: () => LogTarget | null,
    private readonly options: RecorderOptions,
  ) {}

  private content(value: unknown): unknown {
    return this.options.captureContent ? clip(value) : null
  }

  private static session(ctx: HookContext, eventSessionKey?: string): string | undefined {
    return eventSessionKey || ctx.sessionKey || ctx.sessionId || undefined
  }

  private turn(key: string, sessionId: string | undefined): Turn {
    let turn = this.turns.get(key)
    if (turn) {
      this.turns.delete(key) // re-insert: the map's order is least recently used first
    } else {
      turn = { sessionId, hasUser: false, tail: Promise.resolve(null) }
    }
    turn.sessionId ??= sessionId
    this.turns.set(key, turn)
    if (this.turns.size > MAX_TURNS) this.turns.delete(this.turns.keys().next().value as string)
    if (sessionId) {
      this.latestTurn.delete(sessionId)
      this.latestTurn.set(sessionId, key)
      if (this.latestTurn.size > MAX_TURNS) this.latestTurn.delete(this.latestTurn.keys().next().value as string)
    }
    return turn
  }

  private turnFor(ctx: HookContext, runId?: string, eventSessionKey?: string): Turn {
    const sessionId = Recorder.session(ctx, eventSessionKey)
    const key = runId || ctx.runId || (sessionId && this.latestTurn.get(sessionId)) || sessionId || 'default'
    return this.turn(key, sessionId)
  }

  private async write(event: string, data: Data, parentId: string | null, turn: Turn, ctx: HookContext): Promise<string | null> {
    const db = this.db()
    if (!db) return null
    const metadata: Data = { source: 'openclaw' }
    if (ctx.agentId) metadata.openclaw_agent = ctx.agentId
    const channel = ctx.channelId || ctx.channel
    if (channel) metadata.channel = channel
    try {
      const result = await db.log({
        agent: this.options.agent,
        event,
        data,
        ...(parentId ? { parentId } : {}),
        ...(turn.sessionId ? { sessionId: turn.sessionId } : {}),
        metadata,
      })
      return result.eventId
    } catch (err) {
      this.options.warn(`zizkadb: could not record ${event}: ${(err as Error)?.message ?? err}`)
      return null
    }
  }

  /** Append steps to a turn's chain; each step gets the id of the event before it. */
  private chain(turn: Turn, step: (parent: string | null) => Promise<string | null>): void {
    const next = turn.tail.then(async (parent) => (await step(parent)) ?? parent)
    turn.tail = next.catch(() => null)
    const tracked = turn.tail.then(() => {})
    this.pending.add(tracked)
    void tracked.finally(() => this.pending.delete(tracked))
  }

  // ── hooks ──────────────────────────────────────────────────────────────────

  messageReceived(event: { content?: string; from?: string; runId?: string; sessionKey?: string; media?: unknown[] }, ctx: HookContext): void {
    const turn = this.turnFor(ctx, event.runId, event.sessionKey)
    turn.hasUser = true
    const data: Data = { text: this.content(event.content ?? '') }
    if (this.options.captureContent && event.from) data.from = event.from
    if (event.media?.length) data.media_count = event.media.length
    this.chain(turn, () => this.write('user_message', data, null, turn, ctx))
  }

  /** The prompt of a turn no channel message started (CLI, API, cron). */
  private userPrompt(turn: Turn, prompt: string | undefined, ctx: HookContext): void {
    if (turn.hasUser || typeof prompt !== 'string' || !prompt.trim()) return
    turn.hasUser = true
    this.chain(turn, () => this.write('user_message', { text: this.content(prompt) }, null, turn, ctx))
  }

  llmInput(event: { runId?: string; prompt?: string }, ctx: HookContext): void {
    this.userPrompt(this.turnFor(ctx, event.runId), event.prompt, ctx)
  }

  afterToolCall(
    event: { toolName: string; params?: Record<string, unknown>; runId?: string; toolCallId?: string; result?: unknown; error?: string; durationMs?: number },
    ctx: HookContext,
  ): void {
    const turn = this.turnFor(ctx, event.runId)
    const ids = event.toolCallId ? { tool_call_id: event.toolCallId } : {}
    this.chain(turn, async (parent) => {
      const callId = await this.write('tool_call', { tool: event.toolName, ...ids, arguments: this.content(event.params ?? {}) }, parent, turn, ctx)
      const timing = event.durationMs != null ? { duration_ms: Math.round(event.durationMs) } : {}
      const outcome = event.error
        ? this.write('tool_error', { tool: event.toolName, ...ids, error: clip(event.error), ...timing }, callId ?? parent, turn, ctx)
        : this.write('tool_result', { tool: event.toolName, ...ids, result: this.content(event.result), ...timing }, callId ?? parent, turn, ctx)
      return (await outcome) ?? callId
    })
  }

  llmOutput(
    event: { runId?: string; sessionId?: string; provider?: string; model?: string; prompt?: string; assistantTexts?: string[]; usage?: Usage },
    ctx: HookContext,
  ): void {
    const turn = this.turnFor(ctx, event.runId)
    this.userPrompt(turn, event.prompt, ctx)
    const data: Data = { provider: event.provider ?? null, model: event.model ?? null }
    const text = (event.assistantTexts ?? []).filter((t) => typeof t === 'string' && t.length).join('\n\n')
    data.output = this.content(text)
    const u = event.usage
    if (u && (u.input || u.output || u.cacheRead || u.cacheWrite)) {
      data.token_usage = {
        model: event.model ?? null,
        input_tokens: u.input ?? 0,
        output_tokens: u.output ?? 0,
        cached_tokens: u.cacheRead ?? 0,
        ...(u.cacheWrite ? { cache_write_tokens: u.cacheWrite } : {}),
      }
    }
    this.chain(turn, (parent) => this.write('llm_end', data, parent, turn, ctx))
  }

  agentEnd(event: { runId?: string; success: boolean; error?: string; durationMs?: number }, ctx: HookContext): void {
    if (event.success) return
    const turn = this.turnFor(ctx, event.runId)
    const data: Data = { error: clip(event.error ?? 'agent run failed') }
    if (event.durationMs != null) data.duration_ms = Math.round(event.durationMs)
    this.chain(turn, (parent) => this.write('agent_error', data, parent, turn, ctx))
  }

  messageSent(event: { to?: string; content?: string; success: boolean; error?: string; sessionKey?: string; runId?: string }, ctx: HookContext): void {
    const turn = this.turnFor(ctx, event.runId, event.sessionKey)
    const data: Data = { text: this.content(event.content ?? ''), delivered: event.success }
    if (event.error) data.error = clip(event.error)
    this.chain(turn, (parent) => this.write('assistant_response', data, parent, turn, ctx))
  }

  /** Wait for every queued write (call before the gateway stops). */
  async flush(timeoutMs = 5_000): Promise<void> {
    const all = Promise.allSettled([...this.pending])
    let timer: ReturnType<typeof setTimeout> | undefined
    const timeout = new Promise<void>((resolve) => {
      timer = setTimeout(resolve, timeoutMs)
    })
    await Promise.race([all, timeout])
    if (timer) clearTimeout(timer)
  }
}

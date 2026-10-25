/**
 * ZizkaDB TypeScript SDK
 *
 * @example
 * import { ZizkaDB } from 'zizkadb-sdk'
 *
 * // Cloud
 * const db = new ZizkaDB({ apiKey: 'zizkadb_live_xxxx' })
 *
 * // Self-hosted
 * const db = new ZizkaDB({ host: 'http://localhost:8000' })
 *
 * // Log an event
 * const result = await db.log({
 *   agent: 'my-bot',
 *   event: 'tool_call',
 *   data: { tool: 'search', query: '...' },
 * })
 *
 * // Why did something happen?
 * const chain = await db.why(result.eventId)
 * chain.print()
 */

import type {
  ZizkaDBConfig,
  BatchEvent,
  BatchResult,
  AgentEvent,
  AgentInfo,
  AgentState,
  AtOptions,
  CausalChain,
  ChainHead,
  ChainVerification,
  LogOptions,
  LogToolOptions,
  LogToolResultOptions,
  LogApiOptions,
  LogResult,
  QueryOptions,
  SearchOptions,
} from './types'
import { ZizkaDBError, AuthError, NotFoundError, AgentScopeError, RateLimitError } from './types'
import { LineageContext, resolveLogLineage, recordLogResult } from './context'
import { readEnv } from './env'
import packageJson from '../package.json'

export * from './types'
export { LineageContext } from './context'

const CLOUD_HOST = 'https://db.zizka.ai'
const TELEMETRY_URL = 'https://db.zizka.ai/v1/telemetry'
export const SDK_VERSION = packageJson.version
const DEFAULT_DEV_API_KEY = 'zizkadb_dev_local'

function isLocalHost(host: string): boolean {
  const h = host.toLowerCase()
  return h.includes('localhost') || h.includes('127.0.0.1') || h.includes('0.0.0.0')
}


/** ZIZKADB_API_KEY preferred; AGENTDB_API_KEY for legacy managed-cloud users. */
function apiKeyFromEnv(): string | undefined {
  return readEnv('ZIZKADB_API_KEY') ?? readEnv('AGENTDB_API_KEY')
}

function logHintsEnabled(baseUrl: string): boolean {
  const quiet = (readEnv('ZIZKADB_QUIET') ?? '').toLowerCase()
  if (['1', 'true', 'yes', 'on'].includes(quiet)) return false
  if (['1', 'true', 'yes', 'on'].includes((readEnv('ZIZKADB_LOG_HINTS') ?? '').toLowerCase())) {
    return true
  }
  return isLocalHost(baseUrl)
}

function emitLogHint(baseUrl: string, eventId: string): void {
  if (logHintsEnabled(baseUrl)) {
    console.error(`  → zizkadb why ${eventId}`)
  }
}

let _telemetrySent = false

function _isLocalSelfHost(): boolean {
  const host = readEnv('ZIZKADB_HOST') ?? ''
  return (
    host.startsWith('http://localhost') ||
    host.startsWith('http://127.0.0.1') ||
    host.startsWith('http://0.0.0.0')
  )
}

function _sendTelemetry(mode: 'cloud' | 'self-hosted'): void {
  if (_telemetrySent) return
  if (
    /^(false|0|no|off)$/i.test(readEnv('ZIZKADB_TELEMETRY') ?? '')
  ) return

  _telemetrySent = true

  const payload = {
    install_id:  _getInstallId(),
    sdk:         'typescript',
    sdk_version: SDK_VERSION,
    node:        (typeof process !== 'undefined' && process.version) || 'unknown',
    os:          typeof process !== 'undefined' ? process.platform : 'unknown',
    mode,
  }

  // Fire and forget — never awaited, never throws
  try {
    fetch(TELEMETRY_URL, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
      signal:  AbortSignal.timeout(3000),
    }).catch(() => {})
  } catch {
    // ignore
  }
}

function _getInstallId(): string {
  try {
    // Node.js environment — persist to ~/.zizkadb/install_id
    const { homedir } = require('os') as typeof import('os')
    const { mkdirSync, readFileSync, writeFileSync, existsSync } = require('fs') as typeof import('fs')
    const { join } = require('path') as typeof import('path')
    const dir = join(homedir(), '.zizkadb')
    const file = join(dir, 'install_id')
    mkdirSync(dir, { recursive: true })
    if (existsSync(file)) {
      const id = readFileSync(file, 'utf8').trim()
      if (id) return id
    }
    const id = _uuid()
    writeFileSync(file, id)
    return id
  } catch {
    return _stableMachineId()
  }
}

function _stableMachineId(): string {
  try {
    const { readFileSync, existsSync } = require('fs') as typeof import('fs')
    const { createHash } = require('crypto') as typeof import('crypto')
    const { hostname, platform, arch } = require('os') as typeof import('os')
    for (const p of ['/etc/machine-id', '/var/lib/dbus/machine-id']) {
      if (existsSync(p)) {
        const raw = readFileSync(p, 'utf8').trim()
        if (raw) {
          return createHash('sha256').update(`zizkadb:${raw}`).digest('hex').replace(
            /^(.{8})(.{4})(.{4})(.{4})(.{12}).*$/,
            '$1-$2-$3-$4-$5',
          )
        }
      }
    }
    const seed = `${hostname()}:${platform()}:${arch()}`
    return createHash('sha256').update(`zizkadb:${seed}`).digest('hex').replace(
      /^(.{8})(.{4})(.{4})(.{4})(.{12}).*$/,
      '$1-$2-$3-$4-$5',
    )
  } catch {
    return _uuid()
  }
}

function _uuid(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

function formatFastApiDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg: unknown }).msg)
        }
        return JSON.stringify(item)
      })
      .join('; ')
  }
  if (detail && typeof detail === 'object') return JSON.stringify(detail)
  if (detail == null) return fallback
  return String(detail)
}

function lineage(o: { parentId?: string; sessionId?: string; metadata?: Record<string, unknown> }) {
  return { parentId: o.parentId, sessionId: o.sessionId, metadata: o.metadata }
}

function errorFields(error: string | Error): Record<string, unknown> {
  return error instanceof Error
    ? { error: error.message, type: error.name }
    : { error: String(error) }
}

/** `url` without query string, fragment or credentials, plus its host+path. */
function sanitizeUrl(raw: string): { url: string; target: string } {
  try {
    const u = new URL(raw)
    return { url: `${u.protocol}//${u.host}${u.pathname}`, target: `${u.host}${u.pathname}` }
  } catch {
    const bare = raw.split('?')[0].split('#')[0]
    return { url: bare, target: bare }
  }
}

export class ZizkaDB {
  private readonly baseUrl: string
  private readonly headers: Record<string, string>
  private readonly timeout: number
  private readonly telemetryMode: 'cloud' | 'self-hosted'

  constructor(config: ZizkaDBConfig) {
    // When host is omitted, ZIZKADB_HOST (or legacy AGENTDB_HOST) is used if
    // set, so a self-hosted key is never sent to Cloud. An explicit host
    // always wins. Runtimes without `process` (browsers, edge) skip the env.
    // A blank value counts as unset, and each variable is tried in turn.
    const host =
      config.host?.trim() ||
      readEnv('ZIZKADB_HOST')?.trim() ||
      readEnv('AGENTDB_HOST')?.trim() ||
      undefined
    if (!config.apiKey && !host) {
      throw new ZizkaDBError(
        'Provide an apiKey (cloud) or host (self-hosted).\n' +
        '  Cloud:       new ZizkaDB({ apiKey: "zizkadb_live_..." })\n' +
        '  Self-hosted: new ZizkaDB({ host: "http://localhost:8000" })',
      )
    }

    this.baseUrl = host?.replace(/\/$/, '') ?? CLOUD_HOST
    this.timeout = config.timeout ?? 10_000

    let apiKey = config.apiKey
    if (!apiKey && host) {
      if (isLocalHost(host)) {
        apiKey =
          apiKeyFromEnv() ??
          readEnv('DEV_API_KEY') ??
          DEFAULT_DEV_API_KEY
      } else {
        apiKey = apiKeyFromEnv()
        if (!apiKey) {
          throw new ZizkaDBError(
            `Cloud host requires an apiKey or ZIZKADB_API_KEY env var. Host: ${host}`,
          )
        }
      }
    }

    this.headers = {
      'Content-Type': 'application/json',
      ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
    }

    this.telemetryMode = host ? 'self-hosted' : 'cloud'
    _sendTelemetry(this.telemetryMode)
  }

  // ─────────────────────────────────────────
  // LOG
  // ─────────────────────────────────────────

  track(agent: string, sessionId?: string): LineageContext {
    return new LineageContext(this, agent, sessionId)
  }

  async log(options: LogOptions): Promise<LogResult> {
    const explicitParent = options.parentId != null
    const { parentId, sessionId } = resolveLogLineage(
      options.agent,
      options.event,
      options.parentId ?? null,
      options.sessionId ?? null,
      explicitParent,
    )
    const res = await this.post('/v1/events', {
      agent: options.agent,
      event: options.event,
      data: options.data,
      parent_id: parentId,
      session_id: sessionId,
      metadata: options.metadata ?? null,
      ...(options.idempotencyKey != null ? { idempotency_key: options.idempotencyKey } : {}),
      ...(options.supersedes != null ? { supersedes: options.supersedes } : {}),
      ...(options.importance != null ? { importance: options.importance } : {}),
    }) as {
      event_id: string
      timestamp: string
      sequence_no: number
      checksum: string
      index_status?: string
      indexed?: boolean
      chain_hash?: string | null
      duplicate?: boolean
    }
    const result: LogResult = {
      eventId: res.event_id,
      timestamp: new Date(res.timestamp),
      sequenceNo: res.sequence_no,
      checksum: res.checksum,
      indexStatus: res.index_status ?? (res.indexed ? 'indexed' : 'failed'),
      ...(res.chain_hash ? { chainHash: res.chain_hash } : {}),
      ...(res.duplicate ? { duplicate: true } : {}),
    }
    recordLogResult(options.agent, result.eventId)
    emitLogHint(this.baseUrl, result.eventId)
    return result
  }

  /**
   * Log a tool call by name, so the dashboard and reports show *which* tool
   * ran (`tool_call · lookup_order`), not just that one did.
   */
  async logTool(options: LogToolOptions): Promise<LogResult> {
    const data: Record<string, unknown> = { tool: options.tool }
    if (options.args !== undefined) data.args = options.args
    return this.log({ ...lineage(options), agent: options.agent, event: 'tool_call', data })
  }

  /** Log how a tool call ended. With `error`, the event is a `tool_error`. */
  async logToolResult(options: LogToolResultOptions): Promise<LogResult> {
    const data: Record<string, unknown> = { tool: options.tool }
    const failed = options.error !== undefined
    if (failed) Object.assign(data, errorFields(options.error!))
    else if (options.result !== undefined) data.result = options.result
    if (options.durationMs !== undefined) data.duration_ms = options.durationMs
    return this.log({
      ...lineage(options),
      agent: options.agent,
      event: failed ? 'tool_error' : 'tool_result',
      data,
    })
  }

  /**
   * Log an outbound API call, named so reports show *which* API was hit.
   * The query string, fragment and credentials in `url` are dropped before
   * logging. A 4xx/5xx `status` or an `error` counts toward its error rate.
   */
  async logApi(options: LogApiOptions): Promise<LogResult> {
    const method = options.method.toUpperCase()
    const { url, target } = sanitizeUrl(options.url)
    const data: Record<string, unknown> = {
      api: options.name ?? `${method} ${target}`,
      method,
      url,
    }
    if (options.status !== undefined) data.status = options.status
    if (options.durationMs !== undefined) data.duration_ms = options.durationMs
    if (options.error !== undefined) Object.assign(data, errorFields(options.error))
    return this.log({ ...lineage(options), agent: options.agent, event: 'api_call', data })
  }

  // ─────────────────────────────────────────
  // QUERY
  // ─────────────────────────────────────────

  async query(options: QueryOptions): Promise<AgentEvent[]> {
    const params: Record<string, string> = {
      agent: options.agent,
      limit: String(options.limit ?? 50),
    }
    if (options.before) params.before = options.before.toISOString()
    if (options.after) params.after = options.after.toISOString()
    if (options.eventType) params.event_type = options.eventType
    if (options.sessionId) params.session_id = options.sessionId

    const res = await this.get('/v1/events', params) as Record<string, unknown>[]
    return res.map(parseEvent)
  }

  /**
   * Every event of an agent, newest first, page by page with a cursor
   * (fast at any depth, no duplicates or gaps).
   *
   *   for await (const event of db.iterEvents('my-bot')) { ... }
   */
  async *iterEvents(
    agent: string,
    options: { pageSize?: number, eventType?: string, sessionId?: string } = {},
  ): AsyncGenerator<AgentEvent> {
    const pageSize = options.pageSize ?? 500
    let cursor: string | undefined
    while (true) {
      const params: Record<string, string> = { agent, limit: String(pageSize) }
      if (options.eventType) params.event_type = options.eventType
      if (options.sessionId) params.session_id = options.sessionId
      if (cursor) params.cursor = cursor
      const page = await this.get('/v1/events', params) as Record<string, unknown>[]
      for (const item of page) yield parseEvent(item)
      if (page.length < pageSize) return
      // The raw timestamp keeps microseconds; a Date would round them off.
      const last = page[page.length - 1]
      cursor = `${last.timestamp as string}|${last.sequence_no as number}`
    }
  }

  /**
   * Stream every event of an agent, oldest first (the server's NDJSON export).
   * Pass the objects to `importEvents()` on another instance, or save them.
   */
  async *exportEvents(agent: string): AsyncGenerator<Record<string, unknown>> {
    const url = `${this.baseUrl}/v1/events/export?${new URLSearchParams({ agent })}`
    const res = await fetch(url, { method: 'GET', headers: this.headers })
    if (!res.ok || !res.body) {
      await this.handle(res)
      return
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      let newline = buffer.indexOf('\n')
      while (newline >= 0) {
        const line = buffer.slice(0, newline).trim()
        buffer = buffer.slice(newline + 1)
        if (line) yield JSON.parse(line) as Record<string, unknown>
        newline = buffer.indexOf('\n')
      }
      if (done) break
    }
    if (buffer.trim()) yield JSON.parse(buffer) as Record<string, unknown>
  }

  /**
   * Import events from `exportEvents()` (oldest first), 1000 per request.
   * Original timestamps are kept, parents remapped; re-running is safe.
   */
  async importEvents(
    events: Record<string, unknown>[],
    options: { agent?: string, chunkSize?: number } = {},
  ): Promise<{ imported: number, duplicates: number, failed: number, parentsDropped: number, errors: unknown[] }> {
    const size = options.chunkSize ?? 1000
    const totals = { imported: 0, duplicates: 0, failed: 0, parentsDropped: 0, errors: [] as unknown[] }
    for (let start = 0; start < events.length; start += size) {
      const res = await this.post('/v1/events/import', {
        events: events.slice(start, start + size),
        ...(options.agent ? { agent: options.agent } : {}),
      }) as { imported: number, duplicates: number, failed: number, parents_dropped: number, errors: unknown[] }
      totals.imported += res.imported
      totals.duplicates += res.duplicates
      totals.failed += res.failed
      totals.parentsDropped += res.parents_dropped
      totals.errors.push(...res.errors)
    }
    return totals
  }

  /**
   * Write up to 500 events in one request, in order. One bad event does not
   * reject the others: check `results[i].ok`.
   */
  async logBatch(events: BatchEvent[]): Promise<BatchResult> {
    const res = await this.post('/v1/events/batch', {
      events: events.map(e => ({
        agent: e.agent,
        event: e.event,
        data: e.data,
        parent_id: e.parentId ?? null,
        session_id: e.sessionId ?? null,
        metadata: e.metadata ?? null,
        ...(e.idempotencyKey != null ? { idempotency_key: e.idempotencyKey } : {}),
        ...(e.supersedes != null ? { supersedes: e.supersedes } : {}),
        ...(e.importance != null ? { importance: e.importance } : {}),
        ...(e.parentIndex != null ? { parent_index: e.parentIndex } : {}),
      })),
    }) as { written: number, failed: number, results: Record<string, unknown>[] }
    return {
      written: res.written,
      failed: res.failed,
      results: res.results.map(r => ({
        index: r.index as number,
        ok: r.ok as boolean,
        ...(r.ok
          ? { eventId: r.event_id as string, sequenceNo: r.sequence_no as number, duplicate: Boolean(r.duplicate) }
          : { status: r.status as number, error: r.error }),
      })),
    }
  }

  // ─────────────────────────────────────────
  // WHY
  // ─────────────────────────────────────────

  async why(eventId: string, depth = 10): Promise<CausalChain> {
    const res = await this.get(`/v1/events/${eventId}/why`, {
      depth: String(depth),
    }) as {
      event_id: string
      chain_length: number
      chain: Record<string, unknown>[]
      chain_complete?: boolean
      orphan?: boolean
      depth_truncated?: boolean
      scoped_agent_limited?: boolean
    }
    const chain = res.chain.map(parseEvent)
    const chainComplete = res.chain_complete ?? true
    const orphan = res.orphan ?? false
    const depthTruncated = res.depth_truncated ?? false
    const scopedAgentLimited = res.scoped_agent_limited ?? false

    return {
      eventId: res.event_id,
      chainLength: res.chain_length,
      chain,
      chainComplete,
      orphan,
      depthTruncated,
      scopedAgentLimited,
      print() {
        if (chain.length === 0) {
          console.log('(empty chain)')
          return
        }
        if (orphan || depthTruncated || scopedAgentLimited || !chainComplete) {
          const parts: string[] = []
          if (orphan) parts.push('orphan event (missing parent_id)')
          if (depthTruncated) parts.push('depth limit reached')
          if (scopedAgentLimited) parts.push('agent-scoped key limits chain')
          console.warn(`⚠ Incomplete chain: ${parts.join('; ')}`)
        }
        chain.forEach((event: AgentEvent, i: number) => {
          const indent = '    '.repeat(i)
          let connector = ''
          if (i > 0) {
            connector = i === chain.length - 1 ? '└── ' : '├── '
          }
          const data = JSON.stringify(event.data).slice(0, 60)
          const time = event.timestamp.toTimeString().slice(0, 8)
          console.log(`${indent}${connector}${event.event}: ${data}  [${time}]`)
        })
      },
    }
  }

  // ─────────────────────────────────────────
  // SEARCH
  // ─────────────────────────────────────────

  async search(options: SearchOptions): Promise<AgentEvent[]> {
    const res = await this.post('/v1/search', {
      query: options.query,
      agent: options.agent ?? null,
      limit: options.limit ?? 10,
      ...(options.mode ? { mode: options.mode } : {}),
      ...(options.includeSuperseded ? { include_superseded: true } : {}),
    }) as { results: Record<string, unknown>[] }
    return res.results.map(parseEvent)
  }

  // ─────────────────────────────────────────
  // AT (time travel)
  // ─────────────────────────────────────────

  /**
   * Check the agent's audit hash chain: no event removed, inserted, reordered
   * or changed (deletions through forget() are allowed). Store `chainHead`
   * outside ZizkaDB and pass it back as `after` to check only newer events.
   */
  async verifyChain(agent: string, after?: ChainHead): Promise<ChainVerification> {
    const params: Record<string, string> = { agent }
    if (after) {
      params.after_seq = String(after.sequenceNo)
      params.anchor = after.chainHash
    }
    const res = await this.get('/v1/events/chain', params) as {
      agent: string
      intact: boolean
      summary: Record<string, number>
      problems: Record<string, unknown>[]
      chain_head: { sequence_no: number, chain_hash: string } | null
    }
    return {
      agent: res.agent,
      intact: res.intact,
      summary: res.summary,
      problems: res.problems,
      chainHead: res.chain_head
        ? { sequenceNo: res.chain_head.sequence_no, chainHash: res.chain_head.chain_hash }
        : null,
    }
  }

  async at(options: AtOptions): Promise<AgentState> {
    const res = await this.get('/v1/events/at', {
      agent: options.agent,
      timestamp: options.timestamp.toISOString(),
    }) as {
      agent: string
      at: string
      event_count: number
      state: Record<string, unknown>
    }
    return {
      agent: res.agent,
      at: new Date(res.at),
      eventCount: res.event_count,
      state: res.state,
    }
  }

  // ─────────────────────────────────────────
  // CONTEXT FOR — prompt-ready memory injection
  // ─────────────────────────────────────────

  /**
   * Get a formatted memory block ready to inject into a system prompt.
   * Combines recent events + semantically relevant past events.
   *
   * @example
   * const context = await db.contextFor({
   *   agent: 'support-bot',
   *   task: 'user asking about their invoice',
   * })
   * const messages = [
   *   { role: 'system', content: `You are a support agent.\n\n${context}` },
   *   { role: 'user',   content: userMessage },
   * ]
   */
  async contextFor(options: {
    agent: string
    task: string
    maxTokens?: number
    sessionId?: string
  }): Promise<string> {
    const res = await this.post('/v1/memory/context', {
      agent: options.agent,
      task: options.task,
      max_tokens: options.maxTokens ?? 2000,
      session_id: options.sessionId ?? null,
    }) as Record<string, unknown>
    return (res.context as string) ?? ''
  }

  /**
   * Same as contextFor() but returns full metadata including source events and token count.
   */
  async contextForFull(options: {
    agent: string
    task: string
    maxTokens?: number
    sessionId?: string
  }): Promise<{ context: string; eventCount: number; estimatedTokens: number; sources: unknown[] }> {
    const res = await this.post('/v1/memory/context', {
      agent: options.agent,
      task: options.task,
      max_tokens: options.maxTokens ?? 2000,
      session_id: options.sessionId ?? null,
    }) as Record<string, unknown>
    return {
      context: res.context as string,
      eventCount: res.event_count as number,
      estimatedTokens: res.estimated_tokens as number,
      sources: res.sources as unknown[],
    }
  }

  // ─────────────────────────────────────────
  // MEMORY DIFF — what changed after a session
  // ─────────────────────────────────────────

  /**
   * Returns a summary of what happened in a session.
   * Useful after a session ends to understand what patterns emerged.
   *
   * @example
   * const diff = await db.memoryDiff('sess_abc')
   * console.log(diff.summary)
   * if (diff.hasErrors) console.warn('Errors detected in session')
   */
  async memoryDiff(sessionId: string): Promise<{
    sessionId: string
    agent: string
    eventCount: number
    eventTypes: Record<string, number>
    causalDepth: number
    hasErrors: boolean
    durationSeconds: number | null
    newEventTypes: string[]
    summary: string
  }> {
    const res = await this.get(`/v1/memory/diff/${sessionId}`, {}) as Record<string, unknown>
    return {
      sessionId: res.session_id as string,
      agent: res.agent as string,
      eventCount: res.event_count as number,
      eventTypes: res.event_types as Record<string, number>,
      causalDepth: res.causal_depth as number,
      hasErrors: res.has_errors as boolean,
      durationSeconds: res.duration_seconds as number | null,
      newEventTypes: res.new_event_types as string[],
      summary: res.summary as string,
    }
  }

  // ─────────────────────────────────────────
  // FORGET — GDPR compliance
  // ─────────────────────────────────────────

  /**
   * Delete all events where data[filterKey] === filterValue.
   * Also removes vectors from the search index.
   * Use for GDPR right-to-erasure requests.
   *
   * @example
   * const result = await db.forget({ filterKey: 'user_id', filterValue: 'user_123' })
   * console.log(`Deleted ${result.deletedEvents} events`)
   */
  async forget(options: {
    filterKey: string
    filterValue: string
  }): Promise<{ deletedEvents: number; message: string }> {
    const res = await this.delete('/v1/memory/forget', {
      filter_key: options.filterKey,
      filter_value: options.filterValue,
    }) as Record<string, unknown>
    return {
      deletedEvents: res.deleted_events as number,
      message: res.message as string,
    }
  }

  // ─────────────────────────────────────────
  // AGENTS
  // ─────────────────────────────────────────

  async agents(): Promise<AgentInfo[]> {
    const res = await this.get('/v1/agents', {})
    return (res as Record<string, unknown>[]).map((a) => ({
      agent: a.agent as string,
      firstSeen: new Date(a.first_seen as string),
      lastSeen: new Date(a.last_seen as string),
      eventCount: a.event_count as number,
    }))
  }

  // ─────────────────────────────────────────
  // BASELINE — behavioral baseline + drift signal
  // ─────────────────────────────────────────

  /**
   * Get the behavioral baseline for an agent.
   *
   * Splits this agent's sessions into two windows: the most recent N
   * sessions (`recent`) and everything older (`baseline`). Returns event-type
   * distribution, parent->child transition distribution, average session
   * shape, and error rate for each window, plus a drift score (0 = identical,
   * 1 = totally different) and the biggest behavioural changes between the
   * two windows.
   *
   * Use this to answer "has this agent started behaving differently since I
   * shipped v2?" before users notice.
   *
   * @example
   * const b = await db.baseline({ agent: 'support-bot' })
   * if (b.drift && b.drift.score > 0.15) {
   *   console.warn('Drift detected:', b.drift.biggest_changes)
   * }
   */
  async baseline(options: {
    agent: string
    recentWindow?: number
    window?: '24h' | '7d' | '30d'
  }): Promise<Record<string, unknown>> {
    const params: Record<string, string> = {
      recent_window: String(options.recentWindow ?? 50),
    }
    if (options.window) params.window = options.window
    const res = await this.get(`/v1/agents/${options.agent}/baseline`, params)
    return res as Record<string, unknown>
  }

  async tokenUsage(options: {
    agent: string
    from: string
    to: string
    granularity?: 'hour' | 'day' | 'week'
  }): Promise<Record<string, unknown>> {
    const params: Record<string, string> = { from: options.from, to: options.to }
    if (options.granularity) params.granularity = options.granularity
    const res = await this.get(`/v1/agents/${options.agent}/token-usage`, params)
    return res as Record<string, unknown>
  }

  async tokenOptimization(options: {
    agent: string
    from?: string
    to?: string
    granularity?: 'day' | 'week'
  }): Promise<Record<string, unknown>> {
    const params: Record<string, string> = {}
    if (options.from) params.from = options.from
    if (options.to) params.to = options.to
    if (options.granularity) params.granularity = options.granularity
    const res = await this.get(`/v1/agents/${options.agent}/token-optimization`, params)
    return res as Record<string, unknown>
  }

  async suggestions(options: {
    agent: string
    from?: string
    to?: string
    refresh?: boolean
  }): Promise<Record<string, unknown>> {
    const params: Record<string, string> = {}
    if (options.from) params.from = options.from
    if (options.to) params.to = options.to
    if (options.refresh) params.refresh = 'true'
    const res = await this.get(`/v1/agents/${options.agent}/suggestions`, params)
    return res as Record<string, unknown>
  }

  async report(options: {
    agent: string
    from: string
    to: string
    granularity?: 'day' | 'week'
  }): Promise<Record<string, unknown>> {
    const params: Record<string, string> = { from: options.from, to: options.to }
    if (options.granularity) params.granularity = options.granularity
    const res = await this.get(`/v1/agents/${options.agent}/report`, params)
    return res as Record<string, unknown>
  }

  async behaviorChange(options: {
    agent: string
    window?: '24h' | '7d' | '30d' | 'custom'
    fromTs?: string
    toTs?: string
  }): Promise<Record<string, unknown>> {
    const params: Record<string, string> = { window: options.window ?? '7d' }
    if (options.fromTs) params.from_ts = options.fromTs
    if (options.toTs) params.to_ts = options.toTs
    const res = await this.get(`/v1/agents/${options.agent}/behavior-change`, params)
    return res as Record<string, unknown>
  }

  // ─────────────────────────────────────────
  // HTTP HELPERS
  // ─────────────────────────────────────────

  private async post(path: string, body: unknown): Promise<unknown> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeout)

    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: 'POST',
        headers: this.headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      return this.handle(res)
    } finally {
      clearTimeout(timer)
    }
  }

  private async get(
    path: string,
    params: Record<string, string>,
  ): Promise<unknown> {
    const qs = new URLSearchParams(params).toString()
    const url = qs ? `${this.baseUrl}${path}?${qs}` : `${this.baseUrl}${path}`
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeout)

    try {
      const res = await fetch(url, {
        method: 'GET',
        headers: this.headers,
        signal: controller.signal,
      })
      return this.handle(res)
    } finally {
      clearTimeout(timer)
    }
  }

  private async delete(path: string, body: unknown): Promise<unknown> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeout)
    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: 'DELETE',
        headers: this.headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      return this.handle(res)
    } finally {
      clearTimeout(timer)
    }
  }

  private async authErrorMessage(res: Response): Promise<string> {
    // A hardcoded "go to db.zizka.ai/dashboard" is wrong for a self-hoster whose
    // keys live on their own instance, so tailor the message and surface the
    // server's own guidance when it sends any.
    let serverDetail: string | undefined
    try {
      const json = (await res.json()) as Record<string, unknown>
      if (typeof json.detail === 'string') serverDetail = json.detail
    } catch {}

    let base: string
    if (this.baseUrl === CLOUD_HOST) {
      base = 'Invalid API key. Create an agent at db.zizka.ai/dashboard and copy its key.'
    } else {
      base =
        `Invalid API key for the ZizkaDB instance at ${this.baseUrl}. ` +
        'Use a key generated by that instance.'
    }

    if (serverDetail && !base.includes(serverDetail)) {
      return `${base}\nServer: ${serverDetail}`
    }
    return base
  }

  private async handle(res: Response): Promise<unknown> {
    if (res.status === 401) {
      throw new AuthError(await this.authErrorMessage(res))
    }
    if (res.status === 403) {
      let detail = res.statusText
      try {
        const json = await res.json() as Record<string, unknown>
        detail = formatFastApiDetail(json.detail, detail)
      } catch {}
      throw new AgentScopeError(`Agent mismatch (403): ${detail}`)
    }
    if (res.status === 404) {
      throw new NotFoundError('Resource not found')
    }
    if (res.status === 429) {
      throw new RateLimitError('Rate limit exceeded')
    }
    if (res.status >= 400) {
      let detail = res.statusText
      try {
        const json = await res.json() as Record<string, unknown>
        detail = formatFastApiDetail(json.detail, detail)
      } catch {}
      throw new ZizkaDBError(`ZizkaDB error (${res.status}): ${detail}`, res.status)
    }
    return res.json()
  }
}

function parseEvent(e: Record<string, unknown>): AgentEvent {
  return {
    eventId: e.event_id as string,
    agent: e.agent as string,
    timestamp: new Date(e.timestamp as string),
    event: e.event as string,
    data: e.data as Record<string, unknown>,
    parentId: (e.parent_id as string) ?? null,
    sessionId: (e.session_id as string) ?? null,
    sequenceNo: e.sequence_no as number,
    score: e.score as number | undefined,
  }
}

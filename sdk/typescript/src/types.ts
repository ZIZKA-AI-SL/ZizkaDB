export interface ZizkaDBConfig {
  apiKey?: string
  host?: string
  timeout?: number
}

export interface LogOptions {
  agent: string
  event: string
  data: Record<string, unknown>
  parentId?: string
  sessionId?: string
  metadata?: Record<string, unknown>
}

interface LineageOptions {
  parentId?: string
  sessionId?: string
  metadata?: Record<string, unknown>
}

export interface LogToolOptions extends LineageOptions {
  agent: string
  /** Tool name, e.g. "lookup_order" — shown in the dashboard and reports. */
  tool: string
  args?: unknown
}

export interface LogToolResultOptions extends LineageOptions {
  agent: string
  tool: string
  result?: unknown
  /** Set for a failure: logged as `tool_error` and counted in the tool's error rate. */
  error?: string | Error
  durationMs?: number
}

export interface LogApiOptions extends LineageOptions {
  agent: string
  method: string
  /** Query string, fragment and credentials are dropped before logging. */
  url: string
  status?: number
  /** Display name; defaults to "METHOD host/path". */
  name?: string
  durationMs?: number
  error?: string | Error
}

export interface QueryOptions {
  agent: string
  limit?: number
  before?: Date
  after?: Date
  eventType?: string
  sessionId?: string
}

export interface SearchOptions {
  query: string
  agent?: string
  limit?: number
}

export interface AtOptions {
  agent: string
  timestamp: Date
}

export interface AgentEvent {
  eventId: string
  agent: string
  timestamp: Date
  event: string
  data: Record<string, unknown>
  parentId: string | null
  sessionId: string | null
  sequenceNo: number
  score?: number
}

export interface LogResult {
  eventId: string
  timestamp: Date
  sequenceNo: number
  checksum: string
  indexStatus?: string
}

export interface CausalChain {
  eventId: string
  chainLength: number
  chain: AgentEvent[]
  chainComplete?: boolean
  orphan?: boolean
  depthTruncated?: boolean
  scopedAgentLimited?: boolean
  print(): void
}

export interface AgentState {
  agent: string
  at: Date
  eventCount: number
  state: Record<string, unknown>
}

export interface AgentInfo {
  agent: string
  firstSeen: Date
  lastSeen: Date
  eventCount: number
}

export class ZizkaDBError extends Error {
  constructor(
    message: string,
    public statusCode?: number,
  ) {
    super(message)
    this.name = 'ZizkaDBError'
  }
}

export class AuthError extends ZizkaDBError {
  constructor(message: string) {
    super(message, 401)
    this.name = 'AuthError'
  }
}

export class NotFoundError extends ZizkaDBError {
  constructor(message: string) {
    super(message, 404)
    this.name = 'NotFoundError'
  }
}

export class AgentScopeError extends ZizkaDBError {
  constructor(message: string) {
    super(message, 403)
    this.name = 'AgentScopeError'
  }
}

export class RateLimitError extends ZizkaDBError {
  constructor(message: string) {
    super(message, 429)
    this.name = 'RateLimitError'
  }
}

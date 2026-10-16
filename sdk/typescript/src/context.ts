import { AsyncLocalStorage } from 'async_hooks'
import { randomUUID } from 'crypto'

const MID_CHAIN = new Set([
  'tool_call', 'tool_start', 'tool_end', 'tool_result', 'api_call', 'llm_end', 'retrieval',
  'crew_task', 'decision',
])

interface LineageState {
  agent: string
  sessionId: string
  lastEventId: string | null
  forkParentId: string | null
}

const storage = new AsyncLocalStorage<LineageState>()

export class LineageContext {
  private readonly state: LineageState

  constructor(
    private readonly db: { track: (a: string, s?: string) => LineageContext },
    readonly agent: string,
    sessionId?: string,
  ) {
    this.state = {
      agent,
      sessionId: sessionId ?? randomUUID(),
      lastEventId: null,
      forkParentId: null,
    }
  }

  get sessionId(): string {
    return this.state.sessionId
  }

  fork(): string | null {
    this.state.forkParentId = this.state.lastEventId
    return this.state.forkParentId
  }

  endFork(): void {
    this.state.forkParentId = null
  }

  async run<T>(fn: () => Promise<T>): Promise<T> {
    return storage.run(this.state, fn)
  }
}

export function resolveLogLineage(
  agent: string,
  event: string,
  parentId: string | null | undefined,
  sessionId: string | null | undefined,
  explicitParent: boolean,
): { parentId: string | null; sessionId: string | null } {
  const state = storage.getStore()
  let resolvedParent = parentId ?? null
  let resolvedSession = sessionId ?? null

  if (state && state.agent === agent) {
    if (!resolvedSession) resolvedSession = state.sessionId
    if (!explicitParent) {
      resolvedParent = state.forkParentId ?? state.lastEventId ?? null
    }
  } else if (
    /^(1|true|yes|on)$/i.test(process.env.ZIZKADB_STRICT ?? '') &&
    !explicitParent &&
    MID_CHAIN.has(event)
  ) {
    console.warn(`zizkadb: event ${event} logged without parentId outside track()`)
  }

  return { parentId: resolvedParent, sessionId: resolvedSession }
}

export function recordLogResult(agent: string, eventId: string): void {
  const state = storage.getStore()
  if (state && state.agent === agent && state.forkParentId === null) {
    state.lastEventId = eventId
  }
}

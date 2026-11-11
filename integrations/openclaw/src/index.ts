/**
 * ZizkaDB plugin for OpenClaw: a tamper-evident, causal audit trail of what
 * your OpenClaw agent did and why.
 *
 * It only observes: no hook result is returned, so it never blocks, rewrites
 * or delays a message or a tool call.
 */
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/plugin-entry'
import { ZizkaDB } from 'zizkadb-sdk'
import { clip, Recorder, type LogTarget } from './recorder'

export const PLUGIN_ID = 'zizkadb'

export interface ZizkaDBPluginConfig {
  /** ZizkaDB Cloud key (zizkadb_live_…). Or set ZIZKADB_API_KEY. */
  apiKey?: string
  /** Self-hosted ZizkaDB, e.g. http://localhost:8000. Or set ZIZKADB_HOST. */
  host?: string
  /** Agent name in ZizkaDB. Default "openclaw". */
  agent?: string
  /** false: record what happened (tools, models, tokens, errors) but not prompts, replies, tool arguments or results. */
  captureContent?: boolean
}

type Api = Pick<OpenClawPluginApi, 'id' | 'pluginConfig' | 'logger' | 'on' | 'registerTool' | 'registrationMode' | 'config'>

function readConfig(raw: Record<string, unknown> | undefined): ZizkaDBPluginConfig {
  const c = raw ?? {}
  const str = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : undefined)
  return {
    apiKey: str(c.apiKey),
    host: str(c.host),
    agent: str(c.agent),
    captureContent: c.captureContent !== false,
  }
}

function env(name: string): string | undefined {
  const v = typeof process !== 'undefined' ? process.env?.[name] : undefined
  return v && v.trim() ? v.trim() : undefined
}

function text(value: unknown) {
  return { content: [{ type: 'text' as const, text: typeof value === 'string' ? value : JSON.stringify(value, null, 2) }], details: value }
}

export function register(api: Api, makeClient: (config: ZizkaDBPluginConfig) => ZizkaDB = (c) => new ZizkaDB({ apiKey: c.apiKey, host: c.host })): Recorder | undefined {
  // Metadata scans (discovery, setup, CLI help) load the plugin too: only the
  // running gateway records.
  if (api.registrationMode !== 'full') return undefined

  const config = readConfig(api.pluginConfig)
  const agent = config.agent ?? 'openclaw'

  let client: ZizkaDB | null | undefined
  const db = (): ZizkaDB | null => {
    if (client !== undefined) return client
    try {
      client = makeClient(config)
    } catch (err) {
      api.logger.warn(`zizkadb: not recording: ${(err as Error)?.message ?? err}. Set apiKey (or ZIZKADB_API_KEY) for ZizkaDB Cloud, or host for a self-hosted server.`)
      client = null
    }
    return client
  }
  if (!config.apiKey && !config.host && !env('ZIZKADB_API_KEY') && !env('ZIZKADB_HOST')) {
    api.logger.warn('zizkadb: no apiKey or host configured; nothing will be recorded. See https://github.com/ZIZKA-AI-SL/ZizkaDB/tree/main/integrations/openclaw#configure')
  }

  const recorder = new Recorder(db as () => LogTarget | null, {
    agent,
    captureContent: config.captureContent !== false,
    warn: (m) => api.logger.warn(m),
  })

  const entry = (api.config as { plugins?: { entries?: Record<string, { hooks?: { allowConversationAccess?: boolean } }> } })?.plugins?.entries?.[api.id]
  if (entry?.hooks?.allowConversationAccess !== true) {
    api.logger.info(
      `zizkadb: model outputs and token usage are not recorded until you allow it: set plugins.entries.${api.id}.hooks.allowConversationAccess to true. Messages and tool calls are recorded either way.`,
    )
  }

  // Handlers return nothing: observation only.
  api.on('message_received', (event, ctx) => {
    recorder.messageReceived(event, ctx)
  })
  api.on('llm_input', (event, ctx) => {
    recorder.llmInput(event, ctx)
  })
  api.on('after_tool_call', (event, ctx) => {
    recorder.afterToolCall(event, ctx)
  })
  api.on('llm_output', (event, ctx) => {
    recorder.llmOutput(event, ctx)
  })
  api.on('agent_end', (event, ctx) => {
    recorder.agentEnd(event, ctx)
  })
  api.on('message_sent', (event, ctx) => {
    recorder.messageSent(event, ctx)
  })
  api.on('gateway_stop', async () => {
    await recorder.flush()
  })

  // Optional tools: the agent can look up its own record. Off unless the
  // operator allows them in the agent's tool policy.
  api.registerTool(
    {
      name: 'zizkadb_why',
      label: 'ZizkaDB: why',
      description: 'Explain an event in this agent\'s ZizkaDB audit trail: returns the causal chain (the earlier messages, tool calls and model outputs that led to it).',
      parameters: {
        type: 'object',
        additionalProperties: false,
        required: ['event_id'],
        properties: {
          event_id: { type: 'string', minLength: 1, maxLength: 128, description: 'ZizkaDB event id' },
          depth: { type: 'integer', minimum: 1, maximum: 50, description: 'How many steps back to walk (default 10)' },
        },
      },
      async execute(_id: string, input: Record<string, unknown>) {
        const client = db()
        if (!client) throw new Error('ZizkaDB is not configured')
        const chain = await client.why(String(input.event_id), typeof input.depth === 'number' ? input.depth : 10)
        return text({
          event_id: chain.eventId,
          complete: chain.chainComplete ?? true,
          chain: chain.chain.map((e) => ({ event_id: e.eventId, event: e.event, at: e.timestamp, data: clip(e.data) })),
        })
      },
    },
    { name: 'zizkadb_why', optional: true },
  )
  api.registerTool(
    {
      name: 'zizkadb_search',
      label: 'ZizkaDB: search',
      description: 'Search this agent\'s ZizkaDB audit trail for past events (messages, tool calls, model outputs).',
      parameters: {
        type: 'object',
        additionalProperties: false,
        required: ['query'],
        properties: {
          query: { type: 'string', minLength: 1, maxLength: 1000 },
          limit: { type: 'integer', minimum: 1, maximum: 50, description: 'Default 10' },
        },
      },
      async execute(_id: string, input: Record<string, unknown>) {
        const client = db()
        if (!client) throw new Error('ZizkaDB is not configured')
        const events = await client.search({ query: String(input.query), agent, limit: typeof input.limit === 'number' ? input.limit : 10 })
        return text(events.map((e) => ({ event_id: e.eventId, event: e.event, at: e.timestamp, session_id: e.sessionId, data: clip(e.data) })))
      },
    },
    { name: 'zizkadb_search', optional: true },
  )

  return recorder
}

const plugin = {
  id: PLUGIN_ID,
  name: 'ZizkaDB',
  description: 'Tamper-evident, causal audit trail of what your OpenClaw agent did and why: messages, tool calls, model outputs and token usage, linked cause to effect.',
  register(api: OpenClawPluginApi) {
    register(api)
  },
}

export default plugin

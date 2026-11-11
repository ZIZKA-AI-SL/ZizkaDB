/**
 * ZizkaDB plugin for OpenClaw: a tamper-evident, causal audit trail of what
 * your OpenClaw agent did and why.
 *
 * It only observes: no hook result is returned, so it never blocks, rewrites
 * or delays a message or a tool call.
 */
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/plugin-entry'
import { ZizkaDB } from 'zizkadb-sdk'
import { Recorder, type LogTarget } from './recorder'
import { createTools, TOOL_NAMES, type ToolsClient } from './tools'

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

  // Optional tools (off unless allowed in the agent's tool policy). Each
  // invocation gets tools scoped to its own conversation, taken from
  // OpenClaw's trusted context; none are offered without one.
  api.registerTool((ctx) => createTools(ctx, agent, db as () => ToolsClient | null), { names: [...TOOL_NAMES], optional: true })

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

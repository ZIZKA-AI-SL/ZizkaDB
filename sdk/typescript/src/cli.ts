#!/usr/bin/env node
/**
 * zizkadb-sdk CLI (TypeScript SDK).
 *
 *   npx zizkadb-sdk test from-session <session_id> --agent <agent>
 *       [--entry src/agent.ts:answer] [--clients openai,anthropic] [--out tests] [--force]
 *
 * Reads ZIZKADB_API_KEY / ZIZKADB_HOST like the SDK (default host:
 * http://localhost:8000 when no API key is set).
 */

import { ZizkaDB } from './index'
import { buildSnapshot, writeFiles } from './sessionTests'

const USAGE = `Usage:
  npx zizkadb-sdk test from-session <session_id> --agent <agent> [options]

Options:
  --entry <file:function>   your agent function, called with each user input (e.g. src/agent.ts:answer)
  --clients <list>          LLM clients the agent uses: openai, anthropic (default: openai)
  --out <dir>               where to write the test (default: tests)
  --force                   overwrite existing files`

interface Args {
  positional: string[]
  flags: Record<string, string | boolean>
}

export function parseArgs(argv: string[]): Args {
  const positional: string[] = []
  const flags: Record<string, string | boolean> = {}
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a.startsWith('--')) {
      const key = a.slice(2)
      const next = argv[i + 1]
      if (key === 'force' || key === 'help') flags[key] = true
      else if (next !== undefined && !next.startsWith('--')) {
        flags[key] = next
        i++
      } else flags[key] = ''
    } else positional.push(a)
  }
  return { positional, flags }
}

function clientFromEnv(): ZizkaDB {
  const apiKey = process.env.ZIZKADB_API_KEY || process.env.AGENTDB_API_KEY || undefined
  const host = process.env.ZIZKADB_HOST || process.env.AGENTDB_HOST || (apiKey ? undefined : 'http://localhost:8000')
  return new ZizkaDB({ apiKey, host })
}

export async function main(argv: string[], deps: { db?: () => Pick<ZizkaDB, 'iterEvents'>; log?: (s: string) => void } = {}): Promise<number> {
  const log = deps.log ?? ((s: string) => console.log(s))
  const { positional, flags } = parseArgs(argv)
  if (flags.help || positional[0] !== 'test' || positional[1] !== 'from-session' || !positional[2]) {
    log(USAGE)
    return flags.help ? 0 : 2
  }
  const sessionId = positional[2]
  const agent = typeof flags.agent === 'string' ? flags.agent : ''
  if (!agent) {
    log('--agent is required (the agent the session belongs to)\n\n' + USAGE)
    return 2
  }
  const db = (deps.db ?? clientFromEnv)()
  const events = []
  for await (const e of db.iterEvents(agent, { sessionId })) events.push(e)
  events.reverse() // the API returns newest first
  if (!events.length) {
    log(`No events for session ${JSON.stringify(sessionId)} of agent ${JSON.stringify(agent)}. Check both ids (the dashboard's Sessions view shows them).`)
    return 1
  }
  const snapshot = buildSnapshot(sessionId, agent, events)
  const clients = typeof flags.clients === 'string' && flags.clients ? flags.clients.split(',').map((s) => s.trim()) : undefined
  let paths
  try {
    paths = writeFiles(snapshot, typeof flags.out === 'string' && flags.out ? flags.out : 'tests', {
      entry: typeof flags.entry === 'string' ? flags.entry : null,
      clients,
      force: Boolean(flags.force),
    })
  } catch (err) {
    log((err as Error).message)
    return 1
  }
  log(`Wrote ${paths.testPath} and ${paths.snapshotPath}`)
  log(
    `  ${snapshot.user_inputs.length} user input(s), tool path: ${snapshot.expected_tool_calls.join(' -> ') || '(no tools)'}` +
      (snapshot.recorded_errors.length ? `, recorded errors: ${snapshot.recorded_errors.length}` : ''),
  )
  if (typeof flags.entry !== 'string') log('  Next: connect runAgent() in the test to your agent (or rerun with --entry src/agent.ts:answer).')
  log(`  Run it: npx vitest run ${paths.testPath}`)
  return 0
}

if (typeof require !== 'undefined' && require.main === module) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (err) => {
      console.error(err instanceof Error ? err.message : err)
      process.exit(1)
    },
  )
}

/**
 * Turn a recorded session into a vitest regression test
 * (`npx zizkadb-sdk test from-session`).
 *
 * Writes two files, the same layout as the Python CLI:
 * - `<out>/zizkadb_sessions/<name>.json`: user inputs, the tools the agent
 *   called, its final answer, any errors
 * - `<out>/<name>.test.ts`: replays the user inputs through your agent inside
 *   `capture()` and checks the same tools, in the same order, without errors
 */

import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { ERROR_EVENTS, TOOL_EVENTS, toolName } from './testing'
import type { AgentEvent } from './types'

export const SNAPSHOT_DIR = 'zizkadb_sessions'

export interface SessionSnapshot {
  session_id: string
  agent: string
  recorded_at: string
  user_inputs: string[]
  expected_tool_calls: string[]
  recorded_final_output: string | null
  recorded_errors: { event: string; error: string }[]
  event_count: number
}

const CLIENTS: Record<string, { name: string; from: string }> = {
  openai: { name: 'OpenAI', from: 'openai' },
  anthropic: { name: 'Anthropic', from: '@anthropic-ai/sdk' },
}

function textOf(data: Record<string, unknown>): string | null {
  for (const key of ['text', 'content', 'message', 'input', 'query']) {
    const v = data?.[key]
    if (typeof v === 'string' && v) return v
  }
  return null
}

/** Summarise a session's events (oldest first) into what a test checks. */
export function buildSnapshot(sessionId: string, agent: string, events: Pick<AgentEvent, 'event' | 'data'>[]): SessionSnapshot {
  const userInputs: string[] = []
  const tools: string[] = []
  const errors: { event: string; error: string }[] = []
  let finalOutput: string | null = null
  for (const e of events) {
    const data = (e.data ?? {}) as Record<string, unknown>
    if (e.event === 'user_message' && textOf(data)) userInputs.push(textOf(data)!)
    else if (TOOL_EVENTS.has(e.event) && toolName(data)) tools.push(toolName(data)!)
    else if (ERROR_EVENTS.has(e.event)) {
      errors.push({ event: e.event, error: String(data.error ?? data.message ?? JSON.stringify(data)).slice(0, 500) })
    }
    if (['llm_end', 'assistant_response', 'agent_response'].includes(e.event)) {
      const out = (typeof data.output === 'string' && data.output) || textOf(data)
      if (out) finalOutput = out
    }
  }
  return {
    session_id: sessionId,
    agent,
    recorded_at: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
    user_inputs: userInputs,
    expected_tool_calls: tools,
    recorded_final_output: finalOutput,
    recorded_errors: errors,
    event_count: events.length,
  }
}

export function sessionTestName(sessionId: string): string {
  const slug = sessionId.replace(/[^0-9a-zA-Z]+/g, '_').slice(0, 12).replace(/^_+|_+$/g, '').toLowerCase()
  return `session_${slug}`
}

/** Import path from the test file's folder to `target`, without the .ts/.js extension. */
function importPath(fromDir: string, target: string): string {
  let rel = relative(fromDir, resolve(target)).split(sep).join('/')
  rel = rel.replace(/\.(m|c)?(t|j)sx?$/, '')
  return rel.startsWith('.') ? rel : `./${rel}`
}

/** Source of the vitest file for a snapshot. `entry` = `path/to/agent.ts:functionName`. */
export function renderTest(
  snapshot: SessionSnapshot,
  name: string,
  options: { entry?: string | null; clients?: string[]; outDir?: string } = {},
): string {
  const clientKeys = options.clients?.length ? options.clients : ['openai']
  const unknown = clientKeys.filter((k) => !CLIENTS[k])
  if (unknown.length) throw new Error(`unknown --clients value: ${unknown.join(', ')} (use openai, anthropic)`)
  const imports = clientKeys.map((k) => `import ${CLIENTS[k].name} from '${CLIENTS[k].from}'`)
  let runner: string
  let connected: boolean
  if (options.entry) {
    const i = options.entry.lastIndexOf(':')
    const file = i > 0 ? options.entry.slice(0, i) : ''
    const fn = i > 0 ? options.entry.slice(i + 1) : ''
    if (!file || !/^[A-Za-z_$][\w$]*$/.test(fn)) throw new Error("--entry must look like 'src/agent.ts:functionName'")
    imports.push(`import { ${fn} as agent } from '${importPath(options.outDir ?? '.', file)}'`)
    runner = '/** Send one user message to the agent and return its answer. */\nasync function runAgent(userInput: string): Promise<unknown> {\n  return agent(userInput)\n}'
    connected = true
  } else {
    runner = [
      '/**',
      ' * Send one user message to your agent and return its answer. Replace the',
      ' * body with a call into your agent, for example:',
      " *   import { answer } from '../src/agent'",
      ' *   return answer(userInput)',
      ' * (or rerun the command with --entry src/agent.ts:answer), then set',
      ' * AGENT_CONNECTED to true.',
      ' */',
      'async function runAgent(userInput: string): Promise<unknown> {',
      '  return userInput',
      '}',
    ].join('\n')
    connected = false
  }
  const note = snapshot.recorded_errors.length
    ? `// The recorded session ended with an error (${JSON.stringify(snapshot.recorded_errors[0].error.slice(0, 200))}).\n` +
      '// This test passes once the agent handles the same input without one. If the\n' +
      '// recorded tool path was part of the bug, edit EXPECTED_TOOL_CALLS to the path\n' +
      '// the agent should take.'
    : '// If this session was a bad run, edit EXPECTED_TOOL_CALLS to the path the\n' +
      "// agent should take; as recorded, the test locks in today's path."
  const clientList = clientKeys.map((k) => CLIENTS[k].name).join(', ')
  return `/**
 * Regression test generated by \`npx zizkadb-sdk test from-session ${snapshot.session_id}\`.
 *
 * Replays the user input recorded in that session (${snapshot.event_count} events,
 * agent ${JSON.stringify(snapshot.agent)}) through the agent and checks it takes the
 * same tool path without errors. Snapshot: ${SNAPSHOT_DIR}/${name}.json
 */

import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'
import { capture } from 'zizkadb-sdk'
${imports.join('\n')}

const SNAPSHOT = JSON.parse(readFileSync(new URL('./${SNAPSHOT_DIR}/${name}.json', import.meta.url), 'utf8'))

${note}
const EXPECTED_TOOL_CALLS: string[] = SNAPSHOT.expected_tool_calls

const AGENT_CONNECTED = ${connected}

${runner}

it.skipIf(!AGENT_CONNECTED || SNAPSHOT.user_inputs.length === 0)('${name} takes the recorded tool path', async () => {
  const run = await capture({ agent: SNAPSHOT.agent, clients: [${clientList}] }, async () => {
    for (const userInput of SNAPSHOT.user_inputs) await runAgent(userInput)
  })
  expect(run.errors.map((e) => e.data)).toEqual([])
  expect(run.toolCalls).toEqual(EXPECTED_TOOL_CALLS)
})
`
}

/** Write the snapshot and the test; refuse to overwrite unless `force`. */
export function writeFiles(
  snapshot: SessionSnapshot,
  outDir: string,
  options: { entry?: string | null; clients?: string[]; force?: boolean } = {},
): { snapshotPath: string; testPath: string } {
  const name = sessionTestName(snapshot.session_id)
  const snapshotPath = join(outDir, SNAPSHOT_DIR, `${name}.json`)
  const testPath = join(outDir, `${name}.test.ts`)
  for (const p of [snapshotPath, testPath]) {
    if (existsSync(p) && !options.force) throw new Error(`${p} already exists (use --force to overwrite)`)
  }
  const source = renderTest(snapshot, name, { entry: options.entry, clients: options.clients, outDir })
  mkdirSync(dirname(snapshotPath), { recursive: true })
  writeFileSync(snapshotPath, `${JSON.stringify(snapshot, null, 2)}\n`)
  writeFileSync(testPath, source)
  return { snapshotPath, testPath }
}

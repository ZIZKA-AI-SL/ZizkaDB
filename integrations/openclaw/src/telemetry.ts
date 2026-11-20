/**
 * Anonymous install ping, the same one every ZizkaDB package sends (see
 * docs/telemetry.md): one per gateway process, once ZizkaDB is configured,
 * so the plugin is counted as its own package ("openclaw") next to the SDK.
 *
 * Sent: a random install id (shared with the SDK, ~/.zizkadb/install_id), the
 * plugin version, the Node version and OS, cloud or self-hosted, and where it
 * runs (ci, container or machine). Never sent: keys, agent names, events or
 * the server address. Off with ZIZKADB_TELEMETRY=false.
 */
import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { detectEnv } from 'zizkadb-sdk'

export const PLUGIN_VERSION = '0.1.2'
export const TELEMETRY_URL = 'https://db.zizka.ai/v1/telemetry'

let sent = false

export function telemetryDisabled(): boolean {
  return /^(false|0|no|off)$/i.test((process.env.ZIZKADB_TELEMETRY ?? '').trim())
}

function installId(): string {
  try {
    const dir = join(homedir(), '.zizkadb')
    const file = join(dir, 'install_id')
    if (existsSync(file)) {
      const id = readFileSync(file, 'utf8').trim()
      if (id) return id
    }
    mkdirSync(dir, { recursive: true })
    const id = randomUUID()
    writeFileSync(file, id)
    return id
  } catch {
    return randomUUID()
  }
}

export function installPayload(mode: 'cloud' | 'self-hosted') {
  return {
    install_id: installId(),
    sdk: 'openclaw',
    sdk_version: PLUGIN_VERSION,
    node: process.version,
    os: process.platform,
    mode,
    env: detectEnv(),
  }
}

/** Fire and forget: never awaited, never throws, at most once per process. */
export function sendInstallPing(mode: 'cloud' | 'self-hosted', post: typeof fetch = fetch): void {
  if (sent || telemetryDisabled()) return
  sent = true
  try {
    post(TELEMETRY_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(installPayload(mode)),
      signal: AbortSignal.timeout(3000),
    }).catch(() => {})
  } catch {
    // ignore
  }
}

/** Tests only. */
export function _resetTelemetry(): void {
  sent = false
}

import { afterEach, describe, expect, it, vi } from 'vitest'
import { register } from '../src/index'
import { _resetTelemetry, installPayload, sendInstallPing, TELEMETRY_URL } from '../src/telemetry'

function api(config: Record<string, unknown> = {}) {
  return {
    id: 'zizkadb',
    registrationMode: 'full',
    pluginConfig: config,
    config: {},
    logger: { info: vi.fn(), warn: vi.fn() },
    on: vi.fn(),
    registerTool: vi.fn(),
  }
}
const hook = (a: ReturnType<typeof api>, name: string) => a.on.mock.calls.find((c) => c[0] === name)![1]

afterEach(() => {
  _resetTelemetry()
  delete process.env.ZIZKADB_TELEMETRY
})

describe('install ping', () => {
  it('reports the plugin as its own package, with no keys or server address', () => {
    const p = installPayload('self-hosted')
    expect(p.sdk).toBe('openclaw')
    expect(p.mode).toBe('self-hosted')
    expect(['ci', 'container', 'machine']).toContain(p.env)
    expect(Object.keys(p).sort()).toEqual(['env', 'install_id', 'mode', 'node', 'os', 'sdk', 'sdk_version'])
  })

  it('sends once per process and never throws', () => {
    const post = vi.fn(async () => new Response('{}'))
    sendInstallPing('cloud', post as any)
    sendInstallPing('cloud', post as any)
    expect(post).toHaveBeenCalledTimes(1)
    expect((post.mock.calls[0] as any)[0]).toBe(TELEMETRY_URL)
    expect(JSON.parse((post.mock.calls[0] as any)[1].body).sdk).toBe('openclaw')
    _resetTelemetry()
    expect(() => sendInstallPing('cloud', (() => { throw new Error('offline') }) as any)).not.toThrow()
  })

  it('ZIZKADB_TELEMETRY=false turns it off', () => {
    process.env.ZIZKADB_TELEMETRY = 'false'
    const post = vi.fn(async () => new Response('{}'))
    sendInstallPing('cloud', post as any)
    expect(post).not.toHaveBeenCalled()
  })

  it('pings only once ZizkaDB is configured and first used, with the right mode', async () => {
    const ping = vi.fn()
    const a = api({ host: 'http://localhost:8000' })
    register(a as any, () => ({ log: async () => ({ eventId: 'e1' }) }) as any, ping)
    expect(ping).not.toHaveBeenCalled() // loading alone sends nothing
    hook(a, 'llm_input')({ prompt: 'hi', runId: 'r1' }, { sessionKey: 's1' })
    await new Promise((r) => setTimeout(r, 10))
    expect(ping).toHaveBeenCalledWith('self-hosted')
  })

  it('discovery loads send nothing', () => {
    const ping = vi.fn()
    const a = { ...api(), registrationMode: 'discovery' }
    register(a as any, () => { throw new Error('should not build a client') }, ping)
    expect(ping).not.toHaveBeenCalled()
  })
})

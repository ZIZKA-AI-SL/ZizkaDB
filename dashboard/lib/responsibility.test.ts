import { describe, expect, it } from 'vitest'
import { agentVersionOf, eventFailed, firstFailure, modelOf, writerLabel, writtenBy } from './responsibility'

describe('writer', () => {
  it('names the credential that wrote the event', () => {
    expect(writerLabel({ written_by: { kind: 'api_key', key_id: 'k1', key_prefix: 'zizkadb_live_ab12' } })).toBe('API key zizkadb_live_ab12…')
    expect(writerLabel({ written_by: { kind: 'dashboard', user_id: 'u1' } })).toMatch(/^Dashboard user/)
    expect(writerLabel({ written_by: { kind: 'dev_key' } })).toBe('Development key')
  })

  it('says so when an older event has no recorded writer', () => {
    expect(writerLabel(null)).toMatch(/^Not recorded/)
    expect(writerLabel({ team: 'a' })).toMatch(/^Not recorded/)
    expect(writtenBy({ written_by: 'nonsense' })).toBeNull()
  })
})

describe('model and version', () => {
  it('reads the model from data.model or token_usage', () => {
    expect(modelOf({ data: { model: 'gpt-4.1-mini' } })).toBe('gpt-4.1-mini')
    expect(modelOf({ data: { token_usage: { model: 'claude-sonnet-5-5' } } })).toBe('claude-sonnet-5-5')
    expect(modelOf({ data: {} })).toBeNull()
  })

  it('reads the agent version from metadata', () => {
    expect(agentVersionOf({ agent_version: 'prompt-v7' })).toBe('prompt-v7')
    expect(agentVersionOf(null)).toBeNull()
  })
})

describe('firstFailure', () => {
  const ev = (event: string, data: Record<string, unknown> = {}) => ({ event, data })

  it('finds the earliest failed step, by type or by an error in its data', () => {
    const chain = [ev('user_message'), ev('tool_result', { error: 'NotFound' }), ev('agent_error')]
    expect(firstFailure(chain)).toBe(chain[1])
    expect(eventFailed(ev('payment_failed'))).toBe(true)
  })

  it('returns null when nothing failed', () => {
    expect(firstFailure([ev('user_message'), ev('assistant_response')])).toBeNull()
  })
})

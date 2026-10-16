import { describe, it, expect } from 'vitest'
import {
  isErrorEvent,
  eventActionName,
  eventLabel,
  eventColor,
  groupBySession,
  topN,
  normalizeSearchResults,
  deriveStatus,
} from './events'
import { colors } from './design-tokens'
import type { AgentEvent } from './api'

function ev(partial: Partial<AgentEvent>): AgentEvent {
  return {
    event_id: 'e1',
    agent: 'a',
    timestamp: '2026-01-01T00:00:00Z',
    event: 'tool_call',
    data: {},
    parent_id: null,
    session_id: null,
    sequence_no: 1,
    ...partial,
  } as AgentEvent
}

describe('isErrorEvent', () => {
  it.each(['tool_error', 'ERROR', 'retry_failed', 'fail'])(
    'flags %s as an error',
    (t) => expect(isErrorEvent(t)).toBe(true),
  )

  it.each(['tool_call', 'user_message', ''])('does not flag %s', (t) =>
    expect(isErrorEvent(t)).toBe(false),
  )
})

describe('eventColor', () => {
  it('uses danger for errors', () => {
    expect(eventColor('tool_error')).toBe(colors.danger)
  })

  it('uses success for lifecycle events', () => {
    expect(eventColor('session_start')).toBe(colors.success)
  })

  it('falls back to muted for unknown types', () => {
    expect(eventColor('something_unknown')).toBe(colors.textMuted)
  })
})

describe('groupBySession', () => {
  it('groups by session preserving encounter order', () => {
    const groups = groupBySession([
      ev({ event_id: '1', session_id: 's1' }),
      ev({ event_id: '2', session_id: 's2' }),
      ev({ event_id: '3', session_id: 's1' }),
    ])
    expect(groups.map((g) => g.sessionId)).toEqual(['s1', 's2'])
    expect(groups[0].events).toHaveLength(2)
  })

  it('keeps events with a null session (never drops them)', () => {
    const groups = groupBySession([ev({ event_id: '1', session_id: null })])
    expect(groups).toHaveLength(1)
    expect(groups[0].sessionId).toBeNull()
  })

  it('returns [] for no events', () => {
    expect(groupBySession([])).toEqual([])
  })
})

describe('topN', () => {
  it('sorts by count desc and truncates', () => {
    expect(topN({ a: 1, b: 5, c: 3 }, 2)).toEqual([
      ['b', 5],
      ['c', 3],
    ])
  })

  it('handles n greater than length', () => {
    expect(topN({ a: 1 }, 10)).toEqual([['a', 1]])
  })

  it('handles an empty map', () => {
    expect(topN({}, 3)).toEqual([])
  })
})

describe('normalizeSearchResults', () => {
  it('accepts { results: [...] }', () => {
    expect(normalizeSearchResults({ results: [ev({})] })).toHaveLength(1)
  })

  it('accepts a bare array', () => {
    expect(normalizeSearchResults([ev({}), ev({})])).toHaveLength(2)
  })

  it('returns [] for null/undefined/garbage', () => {
    expect(normalizeSearchResults(null)).toEqual([])
    expect(normalizeSearchResults(undefined)).toEqual([])
    expect(normalizeSearchResults({ results: 'nope' })).toEqual([])
  })
})

describe('eventActionName / eventLabel', () => {
  it('names the tool from data.tool or data.tool_name', () => {
    expect(eventActionName({ event: 'tool_call', data: { tool: 'lookup_order' } })).toBe('lookup_order')
    expect(eventActionName({ event: 'tool_error', data: { tool_name: 'refund' } })).toBe('refund')
    expect(eventLabel({ event: 'tool_call', data: { tool: 'lookup_order' } })).toBe(
      'tool_call · lookup_order',
    )
  })

  it('names the API from data.api, or method + url on api events', () => {
    expect(eventActionName({ event: 'api_call', data: { api: 'POST api.stripe.com/v1/refunds' } }))
      .toBe('POST api.stripe.com/v1/refunds')
    expect(eventActionName({ event: 'api_call', data: { method: 'GET', url: 'https://x.test/o' } }))
      .toBe('GET https://x.test/o')
  })

  it('leaves unnamed and unrelated events alone', () => {
    expect(eventActionName({ event: 'tool_call', data: { args: {} } })).toBeNull()
    expect(eventActionName({ event: 'tool_call', data: { tool: { nested: true } } })).toBeNull()
    expect(eventActionName({ event: 'llm_response', data: { method: 'GET', url: 'https://x' } })).toBeNull()
    expect(eventLabel({ event: 'user_message', data: { text: 'hi' } })).toBe('user_message')
  })
})

describe('deriveStatus', () => {
  it('errors win over everything', () => {
    expect(deriveStatus({ lastSeen: new Date().toISOString(), hasErrors: true }))
      .toBe('error')
  })

  it('drift outranks healthy', () => {
    expect(deriveStatus({ lastSeen: new Date().toISOString(), hasDrift: true }))
      .toBe('drift')
  })

  it('recent activity is healthy', () => {
    expect(deriveStatus({ lastSeen: new Date().toISOString() })).toBe('healthy')
  })

  it('stale activity is idle', () => {
    expect(deriveStatus({ lastSeen: '2020-01-01T00:00:00Z' })).toBe('idle')
  })

  it('missing or invalid last-seen is idle', () => {
    expect(deriveStatus({ lastSeen: null })).toBe('idle')
    expect(deriveStatus({ lastSeen: 'not-a-date' })).toBe('idle')
  })
})

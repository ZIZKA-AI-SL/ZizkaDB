import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { AgentStats } from '@/lib/api'

const getAgentStats = vi.fn()

vi.mock('@/lib/api', () => ({
  getAgentStats: (...a: unknown[]) => getAgentStats(...a),
}))

vi.mock('@/lib/auth', () => ({ getToken: () => 'tok' }))

vi.mock('@/lib/constants', () => ({ POLL_INTERVAL_MS: 10_000 }))

import { useAgentStats } from './useAgentStats'

function stats(): AgentStats {
  return {
    total_events: 1,
    sessions: 1,
    top_events: [],
    last_event_at: '2026-01-01T00:00:00Z',
  } as unknown as AgentStats
}

describe('useAgentStats', () => {
  beforeEach(() => {
    getAgentStats.mockReset()
    getAgentStats.mockResolvedValue(stats())
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('loads stats on mount', async () => {
    const { result } = renderHook(() => useAgentStats('a1'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(getAgentStats).toHaveBeenCalledWith('tok', 'a1')
    expect(result.current.stats?.total_events).toBe(1)
  })

  it('does not poll while the tab is hidden', async () => {
    vi.useFakeTimers()
    renderHook(() => useAgentStats('a1'))
    await act(async () => {
      await Promise.resolve()
    })
    expect(getAgentStats).toHaveBeenCalledTimes(1)

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    })
    await act(async () => {
      vi.advanceTimersByTime(10_000)
    })
    expect(getAgentStats).toHaveBeenCalledTimes(1)
  })
})

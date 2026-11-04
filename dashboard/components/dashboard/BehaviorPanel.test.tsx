import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BehaviorPanel } from './BehaviorPanel'
import type { AgentBaseline, BaselineWindow } from '@/lib/api'

const win = (sessions: number): BaselineWindow => ({
  sessions,
  events: sessions * 2,
  event_distribution: { user_message: 0.5, tool_call: 0.5 },
  transitions: { 'user_message→tool_call': 1 },
  avg_events_per_session: 2,
  avg_session_seconds: 30,
  error_rate_pct: 0,
})

const noop = () => {}

describe('BehaviorPanel with a time window', () => {
  it('warming up: shows the server message without count-based advice', () => {
    const baseline: AgentBaseline = {
      agent: 'bot', status: 'warming_up', sessions: 5, recent_window: 50, window: '30d',
      message: 'All 5 sessions are from the last 30d. Drift needs at least one older session to compare against.',
      recent: win(5),
    }
    render(<BehaviorPanel baseline={baseline} agentId="bot" window="30d" onRecompute={noop} />)
    expect(screen.getByText(/All 5 sessions are from the last 30d/)).toBeTruthy()
    expect(screen.queryByText(/more than 50/)).toBeNull()
  })

  it('warming up by count: keeps the session-count advice', () => {
    const baseline: AgentBaseline = {
      agent: 'bot', status: 'warming_up', sessions: 5, recent_window: 50,
      message: 'Need at least 51 sessions to compute drift. You have 5. Keep logging.',
      recent: win(5),
    }
    render(<BehaviorPanel baseline={baseline} agentId="bot" window={undefined} onRecompute={noop} />)
    expect(screen.getByText(/more than 50/)).toBeTruthy()
  })

  it('idle window: says no sessions in the window, not "No baseline yet"', () => {
    const baseline: AgentBaseline = {
      agent: 'bot', status: 'no_current_data', sessions: 300, recent_window: 50, window: '24h',
      message: 'No sessions in the last 24h, so there is nothing recent to compare.',
    }
    render(<BehaviorPanel baseline={baseline} agentId="bot" window="24h" onRecompute={noop} />)
    expect(screen.getByText('No sessions in the last 24h')).toBeTruthy()
    expect(screen.queryByText('No baseline yet')).toBeNull()
  })

  it('ok: labels the split by time, not by the session count', () => {
    const baseline: AgentBaseline = {
      agent: 'bot', status: 'ok', sessions: 5, recent_window: 50, window: '7d',
      baseline: win(3), recent: win(2),
      drift: {
        score: 0.1, behavior_change_pct: 10, verdict: 'minor_drift', biggest_changes: [],
        error_rate_change_pp: 0, session_length_change_pct: 0,
      },
    }
    render(<BehaviorPanel baseline={baseline} agentId="bot" window="7d" onRecompute={noop} />)
    expect(screen.getByText('2 in the last 7d vs 3 earlier')).toBeTruthy()
    expect(screen.queryByText(/50 recent vs/)).toBeNull()
  })

  it('labels follow the window of the data, not the selector', () => {
    // The selector already says 24h while the previous count-based result is
    // still on screen (one render before loading starts).
    const baseline: AgentBaseline = {
      agent: 'bot', status: 'ok', sessions: 300, recent_window: 50, window: null,
      baseline: win(250), recent: win(50),
      drift: {
        score: 0.1, behavior_change_pct: 10, verdict: 'minor_drift', biggest_changes: [],
        error_rate_change_pp: 0, session_length_change_pct: 0,
      },
    }
    render(<BehaviorPanel baseline={baseline} agentId="bot" window="24h" onRecompute={noop} />)
    expect(screen.getByText('50 recent vs 250 prior')).toBeTruthy()
    expect(screen.queryByText(/in the last 24h/)).toBeNull()
  })
})

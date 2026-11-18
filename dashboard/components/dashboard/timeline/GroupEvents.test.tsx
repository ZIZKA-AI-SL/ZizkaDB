import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { AgentEvent } from '@/lib/api'

const getEvents = vi.fn()

vi.mock('@/lib/api', () => ({
  getEvents: (...a: unknown[]) => getEvents(...a),
}))
vi.mock('@/lib/auth', () => ({ getToken: () => 'tok' }))

import { GroupEvents } from './GroupEvents'
import { TimelineChart } from './TimelineChart'
import type { AgentTimeline } from '@/lib/api'

const FROM = Date.UTC(2026, 10, 18, 15, 53)
const group = { type: 'passage_indexed', from: FROM, to: FROM + 60_000, count: 120 }

function events(n: number): AgentEvent[] {
  return Array.from({ length: n }, (_, i) => ({
    event_id: `e${i}`,
    agent: 'research-agent',
    timestamp: new Date(FROM + i * 100).toISOString(),
    event: 'passage_indexed',
    data: { text: `passage ${i}` },
    parent_id: 'p',
    session_id: 's1',
    sequence_no: i,
  }))
}

describe('GroupEvents', () => {
  beforeEach(() => {
    getEvents.mockReset()
    getEvents.mockImplementation(async (_t: string, _a: string, q: Record<string, string>) => events(Number(q.limit)))
  })

  it('lists the events behind a grouped dot and opens one', async () => {
    const onSelect = vi.fn()
    render(<GroupEvents agentId="research-agent" group={group} range={{ from: FROM - 120_000, to: FROM + 180_000 }} onSelect={onSelect} onZoom={() => {}} />)

    await screen.findByText('passage 0')
    expect(getEvents).toHaveBeenCalledWith('tok', 'research-agent', expect.objectContaining({ event_type: 'passage_indexed', limit: '50' }))
    // The window is already the narrowest one: no zoom offered.
    expect(screen.queryByText('Zoom in to this time')).toBeNull()

    fireEvent.click(screen.getByText('passage 3'))
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ event_id: 'e3', event: 'passage_indexed', session_id: 's1' }))
  })

  it('loads more on request', async () => {
    render(<GroupEvents agentId="research-agent" group={group} range={{ from: FROM - 3_600_000, to: FROM + 3_600_000 }} onSelect={() => {}} onZoom={() => {}} />)
    await screen.findByText('passage 49')
    expect(screen.getByText('Zoom in to this time')).toBeTruthy()
    fireEvent.click(screen.getByText('Show 50 more'))
    await screen.findByText('passage 99')
    await waitFor(() => expect(screen.getByText('Show 20 more')).toBeTruthy())
  })
})

describe('TimelineChart grouped dots', () => {
  it('a click lists the group instead of zooming', () => {
    const timeline = {
      agent: 'research-agent', from: '', to: '', bucket_seconds: 60, total: 2747, error_total: 0,
      types: [{ event: 'passage_indexed', count: 2747, errors: 0 }],
      buckets: [{ start: new Date(FROM).toISOString(), total: 2747, errors: 0, by_type: { passage_indexed: 2747 } }],
      events: null, errors: [], errors_truncated: false, point_limit: 1500,
    } as AgentTimeline
    const onGroup = vi.fn()
    const onZoom = vi.fn()
    render(
      <TimelineChart
        timeline={timeline} range={{ from: FROM - 120_000, to: FROM + 180_000 }} lanes={['passage_indexed']}
        selected={null} onSelect={() => {}} onGroup={onGroup} selectedGroup={null} onZoom={onZoom}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /2747 × passage_indexed/ }))
    expect(onGroup).toHaveBeenCalledWith({ type: 'passage_indexed', from: FROM, to: FROM + 60_000, count: 2747 })
    expect(onZoom).not.toHaveBeenCalled()
  })
})

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { AgentTimeline } from '@/lib/api'
import { SpikeChart } from './SpikeChart'

const T = Date.UTC(2026, 10, 18, 15, 0)

const timeline = {
  agent: 'writing-agent', from: '', to: '', bucket_seconds: 3600, total: 980, error_total: 12,
  types: [
    { event: 'fact_reviewed', count: 608, errors: 0 },
    { event: 'evidence_linked', count: 360, errors: 0 },
    { event: 'fact_check_failed', count: 12, errors: 12 },
  ],
  buckets: [
    { start: new Date(T).toISOString(), total: 978, errors: 10, by_type: { fact_reviewed: 608, evidence_linked: 360, fact_check_failed: 10 }, errors_by_type: { fact_check_failed: 10 } },
    { start: new Date(T + 3_600_000).toISOString(), total: 2, errors: 2, by_type: { fact_check_failed: 2 }, errors_by_type: { fact_check_failed: 2 } },
  ],
  events: null, errors: [], errors_truncated: false, point_limit: 1500,
} as AgentTimeline

function chart(props: Partial<Parameters<typeof SpikeChart>[0]> = {}) {
  return render(
    <SpikeChart
      timeline={timeline}
      range={{ from: T - 3_600_000, to: T + 3 * 3_600_000 }}
      lanes={['fact_reviewed', 'evidence_linked', 'fact_check_failed']}
      selectedGroup={null}
      onGroup={() => {}}
      onErrors={() => {}}
      onZoom={() => {}}
      {...props}
    />,
  )
}

describe('SpikeChart', () => {
  it('a colour lists that type in that slot; the red part lists the errors', () => {
    const onGroup = vi.fn()
    const onErrors = vi.fn()
    chart({ onGroup, onErrors })

    fireEvent.click(screen.getByRole('button', { name: /608 × fact_reviewed/ }))
    expect(onGroup).toHaveBeenCalledWith({ type: 'fact_reviewed', from: T, to: T + 3_600_000, count: 608 })

    fireEvent.click(screen.getByRole('button', { name: /^10 errors/ }))
    expect(onErrors).toHaveBeenCalledWith({ from: T, to: T + 3_600_000, count: 10 })
    // A slot with only errors still gets its red spike.
    expect(screen.getByRole('button', { name: /^2 errors/ })).toBeTruthy()
  })

  it('hovering a spike shows its breakdown', () => {
    const { container } = chart()
    const spike = container.querySelectorAll('svg > g')
    fireEvent.pointerEnter(Array.from(spike).find((g) => g.querySelector('[aria-label*="608 × fact_reviewed"]'))!)
    const tip = screen.getByRole('tooltip')
    expect(tip.textContent).toContain('978 events')
    expect(tip.textContent).toContain('10 errors')
    expect(tip.textContent).toContain('fact_reviewed608')
  })
})

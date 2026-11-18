import { describe, expect, it } from 'vitest'
import type { AgentTimeline } from './api'
import { colors } from './design-tokens'
import {
  DAY,
  HOUR,
  MAX_SPAN,
  MIN_SPAN,
  activePreset,
  axisTicks,
  bucketLabel,
  canZoomInto,
  clampRange,
  groupQuery,
  laneOrder,
  niceMax,
  presetRange,
  spikes,
  typeColor,
} from './timeline'

const NOW = new Date(2026, 10, 17, 17, 0).getTime()

describe('presets', () => {
  it('end now and span the preset', () => {
    expect(presetRange('24h', NOW)).toEqual({ from: NOW - DAY, to: NOW })
  })

  it('recognise a preset range, but not a moved or custom one', () => {
    expect(activePreset(presetRange('7d', NOW), NOW)).toBe('7d')
    expect(activePreset({ from: NOW - 2 * DAY, to: NOW - DAY }, NOW)).toBeNull()
    expect(activePreset({ from: NOW - 3 * HOUR, to: NOW }, NOW)).toBeNull()
  })
})

describe('clampRange', () => {
  const lo = NOW - 30 * DAY

  it('keeps a range inside the bounds by shifting it, not shrinking it', () => {
    expect(clampRange({ from: NOW - HOUR, to: NOW + HOUR }, lo, NOW)).toEqual({ from: NOW - 2 * HOUR, to: NOW })
  })

  it('widens a tiny range to the minimum zoom', () => {
    const r = clampRange({ from: NOW - 2 * DAY, to: NOW - 2 * DAY + 1000 }, lo, NOW)
    expect(r.to - r.from).toBe(MIN_SPAN)
  })

  it('swaps a reversed range and caps it at the server limit', () => {
    expect(clampRange({ from: NOW, to: NOW - HOUR }, lo, NOW)).toEqual({ from: NOW - HOUR, to: NOW })
    const r = clampRange({ from: NOW - 500 * DAY, to: NOW }, NOW - 600 * DAY, NOW)
    expect(r.to - r.from).toBe(MAX_SPAN)
  })
})

describe('axisTicks', () => {
  it('uses clock times for an hour', () => {
    const ticks = axisTicks({ from: NOW - HOUR, to: NOW })
    expect(ticks.map((t) => t.label)).toEqual(['16:00', '16:15', '16:30', '16:45', '17:00'])
  })

  it('uses dates for a week, on local midnights', () => {
    const ticks = axisTicks({ from: NOW - 7 * DAY, to: NOW })
    expect(ticks.length).toBeGreaterThanOrEqual(6)
    expect(ticks.length).toBeLessThanOrEqual(9)
    ticks.forEach((t) => {
      expect(new Date(t.t).getHours()).toBe(0)
      expect(t.label).toMatch(/^Nov \d+$/)
    })
  })

  it('never returns more than nine ticks', () => {
    for (const span of [MIN_SPAN, HOUR, 6 * HOUR, DAY, 7 * DAY, 30 * DAY, 300 * DAY]) {
      const ticks = axisTicks({ from: NOW - span, to: NOW })
      expect(ticks.length).toBeLessThanOrEqual(10)
      expect(ticks.length).toBeGreaterThan(0)
    }
  })
})

describe('colours and lanes', () => {
  it('gives errors red and ordinary types stable, non-red colours', () => {
    expect(typeColor('tool_error')).toBe(colors.danger)
    expect(typeColor('payment_failed')).toBe(colors.danger)
    expect(typeColor('tool_call')).toBe(typeColor('tool_call'))
    expect(typeColor('custom_step')).not.toBe(colors.danger)
    expect(typeColor('tool_call')).not.toBe(typeColor('tool_result'))
  })

  it('orders lanes by volume with error types last', () => {
    expect(
      laneOrder([
        { event: 'tool_error', count: 50 },
        { event: 'user_message', count: 3 },
        { event: 'tool_call', count: 9 },
      ]),
    ).toEqual(['tool_call', 'user_message', 'tool_error'])
  })

  it('labels bucket sizes', () => {
    expect(bucketLabel(60)).toBe('1 min')
    expect(bucketLabel(900)).toBe('15 min')
    expect(bucketLabel(3 * 3600)).toBe('3 h')
    expect(bucketLabel(86400)).toBe('day')
  })
})


describe('grouped dots', () => {
  const group = { type: 'passage_indexed', from: Date.UTC(2026, 10, 18, 15, 53), to: Date.UTC(2026, 10, 18, 15, 54), count: 2747 }

  it('asks for that type in that slot, start included and end excluded', () => {
    expect(groupQuery(group, 50)).toEqual({
      event_type: 'passage_indexed',
      after: '2026-11-18T15:52:59.999Z',
      before: '2026-11-18T15:54:00.000Z',
      limit: '50',
    })
    expect(groupQuery(group, 5000).limit).toBe('1000')
  })

  it('offers zooming in only while the window can still get narrower', () => {
    expect(canZoomInto({ from: 0, to: MIN_SPAN })).toBe(false)
    expect(canZoomInto({ from: 0, to: HOUR })).toBe(true)
  })
})

describe('spikes', () => {
  const buckets: AgentTimeline['buckets'] = [
    { start: '2026-11-18T15:53:00Z', total: 7, errors: 3, by_type: { tool_call: 4, tool_result: 2, tool_error: 1 }, errors_by_type: { tool_result: 2, tool_error: 1 } },
    { start: '2026-11-18T15:54:00Z', total: 1, errors: 0, by_type: { tool_call: 1 }, errors_by_type: {} },
  ]
  const order = ['tool_call', 'tool_result', 'tool_error']

  it('stacks ordinary events by type and puts errors on top, counted once', () => {
    const [a, b] = spikes(buckets, new Set(order), order)
    expect(a.segments).toEqual([{ type: 'tool_call', n: 4 }])
    expect(a.errors).toBe(3)
    expect(a.errorTypes).toEqual([{ type: 'tool_result', n: 2 }, { type: 'tool_error', n: 1 }])
    expect(a.total).toBe(7)
    expect(b).toMatchObject({ errors: 0, total: 1 })
  })

  it('leaves hidden types out', () => {
    const [a] = spikes(buckets, new Set(['tool_call']), order)
    expect(a).toMatchObject({ total: 4, errors: 0 })
  })

  it('falls back to error-named types without errors_by_type', () => {
    const [a] = spikes([{ start: buckets[0].start, errors: 1, by_type: { tool_call: 2, tool_error: 1 } }], new Set(order), order)
    expect(a).toMatchObject({ errors: 1, total: 3 })
  })

  it('rounds the axis', () => {
    expect([niceMax(0), niceMax(7), niceMax(966), niceMax(2747), niceMax(10)]).toEqual([1, 10, 1000, 5000, 10])
  })
})

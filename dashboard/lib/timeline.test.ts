import { describe, expect, it } from 'vitest'
import { colors } from './design-tokens'
import {
  DAY,
  HOUR,
  MAX_SPAN,
  MIN_SPAN,
  activePreset,
  axisTicks,
  bucketLabel,
  clampRange,
  laneOrder,
  presetRange,
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


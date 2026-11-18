import { describe, it, expect } from 'vitest'
import { MANAGED_PLANS, managedPlanById, startTrialHref } from './plans'

describe('MANAGED_PLANS', () => {
  it('defines pro and team with matching API key caps', () => {
    const pro = MANAGED_PLANS.find((p) => p.id === 'pro')
    const team = MANAGED_PLANS.find((p) => p.id === 'team')
    expect(pro?.features).toContain('3 agents, one API key each')
    expect(team?.features).toContain('10 agents, one API key each')
  })

  it('managedPlanById returns pro for unknown ids', () => {
    expect(managedPlanById('pro').id).toBe('pro')
    // @ts-expect-error exercising fallback for invalid runtime input
    expect(managedPlanById('unknown').id).toBe('pro')
  })
})

describe('startTrialHref', () => {
  it('sends plan-less CTAs to the plan picker', () => {
    expect(startTrialHref()).toBe('/signup/plan')
  })

  it('sends pro and team CTAs into the funnel with ?plan=', () => {
    expect(startTrialHref('pro')).toBe('/signup?plan=pro')
    expect(startTrialHref('team')).toBe('/signup?plan=team')
  })
})

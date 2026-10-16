import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ActionsTable } from './ActionsTable'

const actions = [
  { kind: 'tool' as const, name: 'lookup_order', calls: 6, errors: 3, error_rate_pct: 50, share_pct: 75 },
  { kind: 'api' as const, name: 'POST api.stripe.com/v1/refunds', calls: 2, errors: 0, error_rate_pct: 0, share_pct: 25 },
]

describe('ActionsTable', () => {
  it('lists each tool and API with calls and error rate', () => {
    render(<ActionsTable actions={actions} />)
    expect(screen.getByText('lookup_order')).toBeTruthy()
    expect(screen.getByText('POST api.stripe.com/v1/refunds')).toBeTruthy()
    expect(screen.getByText('3 (50.0%)')).toBeTruthy()
    expect(screen.getByText('75.0%')).toBeTruthy()
  })

  it('renders nothing when no call is named', () => {
    expect(render(<ActionsTable actions={[]} />).container.firstChild).toBeNull()
    expect(render(<ActionsTable actions={undefined} />).container.firstChild).toBeNull()
  })

  it('caps the rows at limit', () => {
    render(<ActionsTable actions={actions} limit={1} />)
    expect(screen.queryByText('POST api.stripe.com/v1/refunds')).toBeNull()
  })
})

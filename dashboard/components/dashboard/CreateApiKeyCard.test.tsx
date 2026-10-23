import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const createApiKey = vi.fn()
const api = vi.hoisted(() => ({ base: 'http://localhost:8000' }))
vi.mock('@/lib/api', () => ({
  get API() {
    return api.base
  },
  createApiKey: (...a: unknown[]) => createApiKey(...a),
}))
vi.mock('@/lib/auth', () => ({ getToken: () => 'tok' }))

import { CreateApiKeyCard } from './CreateApiKeyCard'

beforeEach(() => {
  createApiKey.mockReset()
  api.base = 'http://localhost:8000'
})

describe('CreateApiKeyCard', () => {
  it('explains that a new key is unassigned until first agent use', () => {
    render(<CreateApiKeyCard />)
    expect(screen.getByText(/unassigned until the first agent uses it/i)).toBeInTheDocument()
  })

  it('creates a key and reveals it once, with the host in the usage snippet', async () => {
    createApiKey.mockResolvedValueOnce({ key: 'zizkadb_live_abc123', prefix: 'zizkadb_live_abc', name: 'my-app' })
    const onCreated = vi.fn()
    render(<CreateApiKeyCard onCreated={onCreated} />)

    fireEvent.change(screen.getByLabelText('API key name'), { target: { value: 'my-app' } })
    fireEvent.click(screen.getByRole('button', { name: /create key/i }))

    await waitFor(() => expect(screen.getByText('zizkadb_live_abc123')).toBeInTheDocument())
    expect(createApiKey).toHaveBeenCalledWith('tok', 'my-app')
    // usage snippet points the app at the local host (the fix for cloud-vs-local 401s)
    expect(screen.getByText(/ZIZKADB_HOST=http:\/\/localhost:8000/)).toBeInTheDocument()
    expect(onCreated).toHaveBeenCalled()
  })

  it('points the snippet at this page when the API shares its origin', async () => {
    api.base = ''
    createApiKey.mockResolvedValueOnce({ key: 'zizkadb_live_same', prefix: 'zizkadb_live_sam', name: 'x' })
    render(<CreateApiKeyCard />)
    fireEvent.click(screen.getByRole('button', { name: /create key/i }))
    await waitFor(() => expect(screen.getByText('zizkadb_live_same')).toBeInTheDocument())
    expect(screen.getByText(new RegExp(`ZIZKADB_HOST=${window.location.origin}`))).toBeInTheDocument()
    expect(screen.queryByText(/ZIZKADB_HOST=http:\/\/localhost:8000/)).toBeNull()
  })

  it('defaults an empty name to "default"', async () => {
    createApiKey.mockResolvedValueOnce({ key: 'zizkadb_live_x', prefix: 'zizkadb_live_x', name: 'default' })
    render(<CreateApiKeyCard />)
    fireEvent.click(screen.getByRole('button', { name: /create key/i }))
    await waitFor(() => expect(createApiKey).toHaveBeenCalledWith('tok', 'default'))
  })

  it('surfaces an error when creation fails', async () => {
    createApiKey.mockRejectedValueOnce(new Error('API key limit reached'))
    render(<CreateApiKeyCard />)
    fireEvent.click(screen.getByRole('button', { name: /create key/i }))
    await waitFor(() => expect(screen.getByText(/API key limit reached/)).toBeInTheDocument())
  })
})

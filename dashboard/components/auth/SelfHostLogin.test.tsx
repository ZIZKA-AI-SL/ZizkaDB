import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const getSelfHostConfig = vi.fn()
const selfHostLogin = vi.fn()
const setToken = vi.fn()

vi.mock('@/lib/api', () => ({
  API: 'http://localhost:8000',
  getSelfHostConfig: () => getSelfHostConfig(),
  selfHostLogin: (t?: string) => selfHostLogin(t),
}))
vi.mock('@/lib/auth', () => ({ setToken: (t: string) => setToken(t) }))
vi.mock('@/components/BrandLogo', () => ({ BrandLogo: () => null }))

import { SelfHostLogin } from './SelfHostLogin'

const assign = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  window.sessionStorage.clear()
  Object.defineProperty(window, 'location', { value: { assign }, writable: true })
})

describe('SelfHostLogin', () => {
  it('one-click: signs in and goes to next', async () => {
    getSelfHostConfig.mockResolvedValue({ self_hosted: true, login: 'one_click' })
    selfHostLogin.mockResolvedValue({ access_token: 'jwt' })
    render(<SelfHostLogin next="/dashboard/reports" />)
    fireEvent.click(await screen.findByRole('button', { name: /open my dashboard/i }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/dashboard/reports'))
    expect(selfHostLogin).toHaveBeenCalledWith(undefined)
    expect(setToken).toHaveBeenCalledWith('jwt')
  })

  it('admin token: sends the token and shows API errors', async () => {
    getSelfHostConfig.mockResolvedValue({ self_hosted: true, login: 'admin_token' })
    selfHostLogin.mockRejectedValue(new Error('Invalid admin token'))
    render(<SelfHostLogin next="/dashboard" />)
    fireEvent.change(await screen.findByLabelText(/admin token/i), { target: { value: ' s3cret ' } })
    fireEvent.click(screen.getByRole('button', { name: /open my dashboard/i }))
    expect(await screen.findByText('Invalid admin token')).toBeTruthy()
    expect(selfHostLogin).toHaveBeenCalledWith('s3cret')
    expect(assign).not.toHaveBeenCalled()
  })

  it('unavailable: explains SELFHOST_ADMIN_TOKEN, no sign-in button', async () => {
    getSelfHostConfig.mockResolvedValue({ self_hosted: true, login: 'unavailable' })
    render(<SelfHostLogin next="/dashboard" />)
    expect(await screen.findByText(/login is turned off/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /open my dashboard/i })).toBeNull()
  })

  it('API not self-hosted: explains DEPLOYMENT_MODE', async () => {
    getSelfHostConfig.mockResolvedValue({ self_hosted: false, login: null })
    render(<SelfHostLogin next="/dashboard" />)
    expect(await screen.findByText(/not running in self-hosted mode/i)).toBeTruthy()
  })

  it('API unreachable: shows retry, which refetches', async () => {
    getSelfHostConfig.mockRejectedValueOnce(new Error('down'))
    getSelfHostConfig.mockResolvedValueOnce({ self_hosted: true, login: 'one_click' })
    render(<SelfHostLogin next="/dashboard" />)
    fireEvent.click(await screen.findByRole('button', { name: /retry/i }))
    expect(await screen.findByRole('button', { name: /open my dashboard/i })).toBeTruthy()
  })

  it('auto: one-click mode signs in without a click', async () => {
    getSelfHostConfig.mockResolvedValue({ self_hosted: true, login: 'one_click' })
    selfHostLogin.mockResolvedValue({ access_token: 'jwt' })
    render(<SelfHostLogin next="/dashboard/activity?agent=support-bot" auto />)
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/dashboard/activity?agent=support-bot'))
    expect(selfHostLogin).toHaveBeenCalledTimes(1)
    expect(setToken).toHaveBeenCalledWith('jwt')
  })

  it('auto: a second visit right after falls back to the button (no redirect loop)', async () => {
    getSelfHostConfig.mockResolvedValue({ self_hosted: true, login: 'one_click' })
    selfHostLogin.mockResolvedValue({ access_token: 'jwt' })
    const first = render(<SelfHostLogin next="/dashboard" auto />)
    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1))
    first.unmount()

    render(<SelfHostLogin next="/dashboard" auto />)
    expect(await screen.findByRole('button', { name: /open my dashboard/i })).toBeTruthy()
    expect(selfHostLogin).toHaveBeenCalledTimes(1)
  })

  it('auto: never signs in by itself when an admin token is required', async () => {
    getSelfHostConfig.mockResolvedValue({ self_hosted: true, login: 'admin_token' })
    render(<SelfHostLogin next="/dashboard" auto />)
    await screen.findByLabelText(/admin token/i)
    expect(selfHostLogin).not.toHaveBeenCalled()
  })

  it('never shows signup, email or website links', async () => {
    getSelfHostConfig.mockResolvedValue({ self_hosted: true, login: 'one_click' })
    const { container } = render(<SelfHostLogin next="/dashboard" />)
    await screen.findByRole('button', { name: /open my dashboard/i })
    expect(container.querySelector('a')).toBeNull()
    expect(container.textContent).not.toMatch(/sign up|create .*account|email/i)
  })
})

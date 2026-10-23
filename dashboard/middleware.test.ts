import { afterEach, describe, expect, it, vi } from 'vitest'
import { NextRequest } from 'next/server'
import { USER_TOKEN_COOKIE } from '@/lib/session-cookies'

const ORIGINAL_MODE = process.env.NEXT_PUBLIC_DEPLOYMENT_MODE

// IS_SELF_HOSTED is read at module load, so re-import per deployment mode.
async function loadMiddleware(mode: string | undefined) {
  if (mode === undefined) delete process.env.NEXT_PUBLIC_DEPLOYMENT_MODE
  else process.env.NEXT_PUBLIC_DEPLOYMENT_MODE = mode
  vi.resetModules()
  return (await import('./middleware')).middleware
}

function request(path: string, token?: string) {
  const req = new NextRequest(new URL(path, 'http://localhost:3001'))
  if (token) req.cookies.set(USER_TOKEN_COOKIE, token)
  return req
}

function location(res: Response) {
  const loc = res.headers.get('location')
  return loc ? new URL(loc).pathname + new URL(loc).search : null
}

afterEach(() => {
  if (ORIGINAL_MODE === undefined) delete process.env.NEXT_PUBLIC_DEPLOYMENT_MODE
  else process.env.NEXT_PUBLIC_DEPLOYMENT_MODE = ORIGINAL_MODE
  vi.resetModules()
})

describe('middleware — self-hosted', () => {
  it('sends a signed-out visitor from / to /login', async () => {
    const mw = await loadMiddleware('self_hosted')
    expect(location(mw(request('/')))).toBe('/login')
  })

  it('sends a signed-in visitor from / straight to /dashboard', async () => {
    const mw = await loadMiddleware('self_hosted')
    expect(location(mw(request('/', 'jwt')))).toBe('/dashboard')
  })

  it.each(['/signup', '/signup/plan', '/signup/checkout/return', '/trust', '/eu-ai-act', '/privacy', '/community', '/docs'])(
    'redirects website route %s to /login',
    async (path) => {
      const mw = await loadMiddleware('self_hosted')
      expect(location(mw(request(path)))).toBe('/login')
    },
  )

  it('serves /login without redirecting', async () => {
    const mw = await loadMiddleware('self_hosted')
    expect(location(mw(request('/login')))).toBeNull()
  })

  it('still protects the dashboard and preserves ?next', async () => {
    const mw = await loadMiddleware('self_hosted')
    expect(location(mw(request('/dashboard/reports')))).toBe('/login?next=%2Fdashboard%2Freports')
    expect(location(mw(request('/dashboard/reports', 'jwt')))).toBeNull()
  })

  it('keeps the query string of a deep link in ?next', async () => {
    const mw = await loadMiddleware('self_hosted')
    expect(location(mw(request('/dashboard/activity?agent=support-bot')))).toBe(
      '/login?next=%2Fdashboard%2Factivity%3Fagent%3Dsupport-bot',
    )
  })

  it('marks every response noindex', async () => {
    const mw = await loadMiddleware('self_hosted')
    expect(mw(request('/login')).headers.get('x-robots-tag')).toBe('noindex, nofollow')
  })
})

describe.each([['managed'], [undefined]])('middleware — deployment mode %s (website)', (mode) => {
  it('serves the marketing homepage and signup', async () => {
    const mw = await loadMiddleware(mode)
    expect(location(mw(request('/')))).toBeNull()
    expect(location(mw(request('/signup')))).toBeNull()
    expect(mw(request('/')).headers.get('x-robots-tag')).toBeNull()
  })

  it('still protects the dashboard', async () => {
    const mw = await loadMiddleware(mode)
    expect(location(mw(request('/dashboard')))).toBe('/login?next=%2Fdashboard')
  })
})

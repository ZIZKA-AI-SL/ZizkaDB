import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
import { USER_TOKEN_COOKIE } from '@/lib/session-cookies'
import { IS_SELF_HOSTED } from '@/lib/constants'

function withNoIndex(response: NextResponse): NextResponse {
  response.headers.set('X-Robots-Tag', 'noindex, nofollow')
  return response
}

// Paths next.config.mjs forwards to the API. They pass through untouched so the
// rewrite can run (middleware runs before rewrites).
const API_PROXY_PREFIXES = ['/v1', '/health', '/swagger']

function isApiProxyPath(pathname: string): boolean {
  return API_PROXY_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`))
}

// Self-hosted builds serve only the owner's dashboard: the marketing site,
// signup, pricing and docs (marketing nav) live on the managed website.
function isSelfHostAllowed(pathname: string): boolean {
  return (
    pathname === '/login' ||
    pathname === '/dashboard' ||
    pathname.startsWith('/dashboard/')
  )
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl

  if (isApiProxyPath(pathname)) return NextResponse.next()

  if (IS_SELF_HOSTED && !isSelfHostAllowed(pathname)) {
    // `/` (and any marketing/signup URL) lands on the dashboard; the
    // dashboard check below sends signed-out users to /login.
    const target = request.cookies.get(USER_TOKEN_COOKIE)?.value?.trim()
      ? '/dashboard'
      : '/login'
    return withNoIndex(NextResponse.redirect(new URL(target, request.url)))
  }

  // Tenant dashboard — must be signed in (OTP or self-host dev token)
  if (pathname === '/dashboard' || pathname.startsWith('/dashboard/')) {
    const token = request.cookies.get(USER_TOKEN_COOKIE)?.value?.trim()
    if (!token) {
      const login = new URL('/login', request.url)
      // Keep the query so deep links (?agent=...) survive sign-in.
      login.searchParams.set('next', pathname + request.nextUrl.search)
      return withNoIndex(NextResponse.redirect(login))
    }
    return withNoIndex(NextResponse.next())
  }

  if (IS_SELF_HOSTED) return withNoIndex(NextResponse.next())
  return NextResponse.next()
}

export const config = {
  // Every page route; skips Next internals and files with an extension
  // (robots.txt, sitemap.xml, icon.png, static assets).
  matcher: ['/((?!_next/|.*\\..*).*)'],
}

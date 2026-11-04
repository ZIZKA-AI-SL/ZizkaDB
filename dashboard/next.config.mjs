/** @type {import('next').NextConfig} */
const apiUpstream = process.env.API_REWRITE_TARGET || 'http://127.0.0.1:8000'

const nextConfig = {
  // NEXT_PUBLIC_API_URL can be set at build time for external API hosts.
  // If not set, relative URLs are used and Nginx routes /v1/ to FastAPI.
  output: 'standalone',
  // Standalone mode needs `sharp` for next/image server-side optimization;
  // skip it rather than add a native-binary dependency for a static logo.
  images: { unoptimized: true },
  async headers() {
    // Clickjacking / sniffing protection. No script CSP: it would need a
    // nonce pipeline and could break pages; frame-ancestors is enough here.
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
          { key: 'Content-Security-Policy', value: "frame-ancestors 'self'" },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
        ],
      },
    ]
  },
  async redirects() {
    return [
      { source: '/api-explorer', destination: '/swagger', permanent: false },
      { source: '/api-explorer/:path*', destination: '/swagger', permanent: false },
    ]
  },
  async rewrites() {
    // /swagger does not match nginx location /api/ — works when / → PM2
    return [
      { source: '/swagger', destination: `${apiUpstream}/swagger` },
      { source: '/swagger/:path*', destination: `${apiUpstream}/swagger/:path*` },
      { source: '/openapi.json', destination: `${apiUpstream}/openapi.json` },
    ]
  },
}

export default nextConfig

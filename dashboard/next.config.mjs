/** @type {import('next').NextConfig} */
const apiUpstream = process.env.API_REWRITE_TARGET || 'http://127.0.0.1:8000'

const nextConfig = {
  // NEXT_PUBLIC_API_URL can be set at build time for external API hosts.
  // If not set, relative URLs are used and Nginx routes /v1/ to FastAPI.
  output: 'standalone',
  // Standalone mode needs `sharp` for next/image server-side optimization;
  // skip it rather than add a native-binary dependency for a static logo.
  images: { unoptimized: true },
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
      // Same-origin API for builds with an empty NEXT_PUBLIC_API_URL (the
      // single-container image): one public port serves dashboard and API.
      // Behind nginx, /v1/ never reaches Next.js, so this is inert there.
      { source: '/v1/:path*', destination: `${apiUpstream}/v1/:path*` },
      { source: '/health', destination: `${apiUpstream}/health` },
      { source: '/health/:path*', destination: `${apiUpstream}/health/:path*` },
    ]
  },
}

export default nextConfig

'use strict'
// Single public port for the all-in-one image: API paths go to FastAPI, the
// rest to the Next.js dashboard. Node core only, no dependencies.
//
// It behaves like a standard reverse proxy: the address of whoever connected
// is appended to X-Forwarded-For (never replaced), so the API's client_ip()
// walks the chain from the right and a client cannot pick the address its
// rate limits are keyed on. X-Forwarded-Proto/Host are kept only when they
// come from a proxy listed in TRUSTED_PROXIES; otherwise this proxy sets them.

const http = require('node:http')
const net = require('node:net')

const LISTEN_PORT = Number(process.env.FRONT_PORT || 3001)
const API = { host: '127.0.0.1', port: Number(process.env.API_INTERNAL_PORT || 8000) }
const WEB = { host: '127.0.0.1', port: Number(process.env.DASHBOARD_INTERNAL_PORT || 3002) }
const API_PREFIXES = ['/v1', '/health', '/swagger', '/openapi.json', '/api-explorer']

// Hop-by-hop headers (RFC 9110 §7.6.1) are not forwarded. Transfer-Encoding
// is kept on requests so Node re-frames a chunked body for any method.
const HOP_BY_HOP = [
  'connection', 'keep-alive', 'proxy-connection', 'proxy-authenticate',
  'proxy-authorization', 'te', 'trailer', 'upgrade',
]

// A fresh connection per request: reusing a pooled socket races the
// upstream's keep-alive timeout and turns a healthy request into a 502.
const agent = new http.Agent({ keepAlive: false })

const trusted = new net.BlockList()
for (const entry of (process.env.TRUSTED_PROXIES || '127.0.0.1,::1').split(',')) {
  const item = entry.trim()
  if (!item) continue
  const [addr, bits] = item.split('/')
  const type = net.isIPv6(addr) ? 'ipv6' : net.isIPv4(addr) ? 'ipv4' : null
  if (!type) continue
  if (bits === undefined) trusted.addAddress(addr, type)
  else trusted.addSubnet(addr, Number(bits), type)
}

function isTrusted(addr) {
  if (net.isIPv4(addr)) return trusted.check(addr, 'ipv4')
  if (net.isIPv6(addr)) return trusted.check(addr, 'ipv6')
  return false
}

function isApiPath(url) {
  const path = (url || '/').split('?', 1)[0]
  return API_PREFIXES.some((p) => path === p || path.startsWith(`${p}/`))
}

function peerAddress(socket) {
  const addr = socket.remoteAddress || ''
  // IPv4 clients on a dual-stack socket arrive as ::ffff:a.b.c.d.
  return addr.startsWith('::ffff:') ? addr.slice(7) : addr
}

// The upstreams build absolute redirects from the address they listen on
// (Next.js uses its own host and port, not the request's Host), which no
// browser can reach. Such a Location becomes a relative path, so the browser
// stays on whatever public address it used.
const INTERNAL_PORTS = new Set([String(API.port), String(WEB.port)])
const INTERNAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '0.0.0.0', '[::]'])
const BASE = 'http://front-proxy.invalid'

function publicLocation(location, ports = INTERNAL_PORTS) {
  if (!location) return location
  // A base resolves protocol-relative values (//host:port/path) too.
  const url = new URL(location, BASE)
  if (url.origin === BASE) return location // path-relative already
  const port = url.port || (url.protocol === 'https:' ? '443' : '80')
  if (!INTERNAL_HOSTS.has(url.hostname) || !ports.has(port)) return location
  // Collapse leading slashes: '//evil.example' would leave this site.
  return `/${url.pathname.replace(/^\/+/, '')}${url.search}${url.hash}`
}

function withoutHopByHop(headers, extra = []) {
  const out = { ...headers }
  for (const name of [...HOP_BY_HOP, ...extra]) delete out[name]
  return out
}

const server = http.createServer((req, res) => {
  const api = isApiPath(req.url)
  const peer = peerAddress(req.socket)
  const headers = withoutHopByHop(req.headers)

  const prior = req.headers['x-forwarded-for']
  headers['x-forwarded-for'] = prior ? `${prior}, ${peer}` : peer
  if (!isTrusted(peer) || !headers['x-forwarded-proto']) headers['x-forwarded-proto'] = 'http'
  if (!isTrusted(peer) || !headers['x-forwarded-host']) {
    if (req.headers.host) headers['x-forwarded-host'] = req.headers.host
    else delete headers['x-forwarded-host']
  }

  const upstream = http.request(
    { ...(api ? API : WEB), agent, method: req.method, path: req.url, headers },
    (upRes) => {
      // Node re-frames the response itself, so its Transfer-Encoding is dropped.
      const out = withoutHopByHop(upRes.headers, ['transfer-encoding'])
      // Self-hosted instances are private: keep API responses out of search
      // engines, as the dashboard middleware does for its pages.
      if (api) out['x-robots-tag'] = 'noindex, nofollow'
      if (out.location) out.location = publicLocation(out.location)
      res.writeHead(upRes.statusCode || 502, out)
      upRes.on('error', () => res.destroy())
      upRes.on('aborted', () => res.destroy())
      upRes.pipe(res)
    },
  )
  upstream.on('error', () => {
    // Mid-response: cut the connection rather than append text to the body.
    if (res.headersSent) return res.destroy()
    res.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' })
    res.end('ZizkaDB is starting or a service is unavailable. Try again shortly.\n')
  })
  // Client went away before the response finished: stop the upstream call.
  res.on('close', () => {
    if (!res.writableFinished) upstream.destroy()
  })
  req.pipe(upstream)
})

if (require.main === module) {
  server.listen(LISTEN_PORT, '0.0.0.0')
}

module.exports = { publicLocation }

'use strict'
// Run: node --test infra/allinone/
const test = require('node:test')
const assert = require('node:assert/strict')
const { publicLocation, parseTrusted, hostAllowed } = require('./front-proxy.js')

const ports = new Set(['8000', '3002'])

test('internal redirects become relative paths', () => {
  assert.equal(publicLocation('http://localhost:3002/login?next=%2Fdashboard', ports), '/login?next=%2Fdashboard')
  assert.equal(publicLocation('https://localhost:3002/login', ports), '/login')
  assert.equal(publicLocation('http://127.0.0.1:8000/v1/x#frag', ports), '/v1/x#frag')
  assert.equal(publicLocation('http://[::1]:3002/a', ports), '/a')
  assert.equal(publicLocation('//localhost:3002/login', ports), '/login')
})

test('default ports are matched', () => {
  const web = new Set(['80', '443'])
  assert.equal(publicLocation('http://localhost/login', web), '/login')
  assert.equal(publicLocation('https://127.0.0.1/login', web), '/login')
})

test('a rewrite never leaves the site', () => {
  assert.equal(publicLocation('http://localhost:3002//evil.example/x', ports), '/evil.example/x')
  assert.equal(publicLocation('http://localhost:3002/\\evil.example', ports), '/evil.example')
})

test('other redirects are untouched', () => {
  assert.equal(publicLocation('https://github.com/Zizka-ai', ports), 'https://github.com/Zizka-ai')
  assert.equal(publicLocation('/already-relative', ports), '/already-relative')
  assert.equal(publicLocation('login', ports), 'login')
  assert.equal(publicLocation('http://localhost:9999/other', ports), 'http://localhost:9999/other')
  assert.equal(publicLocation('http://zizka.example.com:3002/x', ports), 'http://zizka.example.com:3002/x')
  assert.equal(publicLocation(undefined, ports), undefined)
})

test('a redirect the URL parser rejects is passed on, never thrown', () => {
  assert.equal(publicLocation('http://1.2.3.999/health', ports), 'http://1.2.3.999/health')
  assert.equal(publicLocation('http://a:99999999/x', ports), 'http://a:99999999/x')
})

test('TRUSTED_PROXIES: invalid entries are skipped, never trusted', () => {
  const list = parseTrusted('127.0.0.1,10.0.0.0/abc,10.1.0.0/33,10.2.0.0/,not-an-ip,192.168.0.0/16')
  assert.equal(list.check('127.0.0.1', 'ipv4'), true)
  assert.equal(list.check('192.168.1.5', 'ipv4'), true)
  // '10.2.0.0/' must not become /0 (trust everything)
  assert.equal(list.check('8.8.8.8', 'ipv4'), false)
  assert.equal(list.check('10.0.0.5', 'ipv4'), false)
})

test('local mode answers only on local host names', () => {
  assert.equal(hostAllowed('localhost:3001', true, null), true)
  assert.equal(hostAllowed('127.0.0.1:3001', true, null), true)
  assert.equal(hostAllowed('[::1]:3001', true, null), true)
  assert.equal(hostAllowed('evil.example:3001', true, null), false)
  assert.equal(hostAllowed('tunnel.example.com', true, 'tunnel.example.com'), true)
  assert.equal(hostAllowed(undefined, true, null), false)
  // production mode does not restrict host names
  assert.equal(hostAllowed('zizka.example.com', false, null), true)
})

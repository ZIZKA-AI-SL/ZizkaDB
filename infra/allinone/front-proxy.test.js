'use strict'
// Run: node --test infra/allinone/
const test = require('node:test')
const assert = require('node:assert/strict')
const { publicLocation } = require('./front-proxy.js')

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

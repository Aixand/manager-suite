// Offline execution of the shipped native probe; fetch and native context are fixtures.
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { inspect, openrouterObservation } from '../../agent_manager/integrations/native/probe.js'
import plugin from '../../agent_manager/integrations/native/index.js'
import { BudgetProbe } from '../../agent_manager/integrations/native/rpc.js'

// Python's NativeProbeTests supplies compiled TOML rules on stdin. The fixture
// never consults user configuration or duplicates the service discovery catalog.
const rules = JSON.parse(readFileSync(0, 'utf8'))
const probe = (ctx, options) => inspect(ctx, { rules, ...options })

const secret = 'fixture-only-native-secret'
function fixture() {
  const calls = { resolve: [], fetch: [], active: [] }
  const providers = ['openai', 'openrouter'].map((id) => ({ id, integrationID: id,
    package: `@opencode/ai/providers/${id}`, settings: {}, headers: {} }))
  const connections = {
    openai: { id: 'cred_openai', type: 'credential', method: 'oauth' },
    openrouter: { id: 'cred_router', type: 'credential', method: 'key' },
  }
  const ctx = {
    app: { version: '2.0.18' },
    provider: { list: async () => ({ data: providers }) },
    integration: { list: async () => ({ data: [{ id: 'openai' }, { id: 'openrouter' }] }), connection: {
      active: async (id) => { calls.active.push(id); return connections[id] },
      resolve: async (connection) => { calls.resolve.push(connection); return { type: 'key', key: secret } },
    } },
    model: { default: async () => ({ providerID: 'openrouter', modelID: 'openai/native' }) },
  }
  const fetcher = async (url, options) => {
    calls.fetch.push(url)
    assert.equal(url, 'https://openrouter.ai/api/v1/key')
    assert.equal(options.redirect, 'error')
    assert.equal(options.headers.Authorization, `Bearer ${secret}`)
    return Response.json({ data: { label: secret, limit: 100, limit_remaining: 75, usage: 25,
      budgets: [{ guardrail_id: 'budget-stable', scope: 'workspace', limit_usd: 50, remaining_usd: 10, spend_usd: 40 }] } })
  }
  return { ctx, providers, connections, fetcher, calls }
}

test('the shipped entrypoint imports without installed SDK packages and registers its RPC', async () => {
  const f = fixture()
  let handler, disposed = false
  f.ctx.rpc = { register: async (definition, handlers) => {
    assert.equal(definition, BudgetProbe)
    assert.equal(definition.id, 'aixand.agent.budgets.v2')
    // OpenCode's actual registry enumerates this property during registration.
    assert.deepEqual(Object.entries(definition.events), [])
    // Real 2.0.18 rejects regex keywords while compiling JSON-Schema RPC input.
    // Identifier restrictions are enforced by the handler instead.
    const input = JSON.stringify(definition.methods.read.input)
    assert.ok(!input.includes('"pattern":'))
    assert.ok(!input.includes('"patternProperties":'))
    handler = handlers.read
    return { dispose: async () => { disposed = true } }
  } }
  assert.equal(plugin.id, 'aixand.agent.budget-probe')
  const cleanup = await plugin.setup(f.ctx)
  const result = await handler({ rules: rules.filter((row) => row.service_id === 'openai') },
    { signal: new AbortController().signal })
  assert.equal(result.schema, 'agent-manager/opencode-bridge-2')
  assert.equal(result.integrations[0].budget.reason, 'unreviewed-route')
  assert.equal(f.calls.resolve.length, 0)
  await assert.rejects(handler({ rules: [{ ...rules[0], id: 'invalid/id' }] },
    { signal: new AbortController().signal }), /Invalid service discovery identifier/)
  await cleanup()
  assert.equal(disposed, true)
})

test('native credentials stay local, key budgets are public, and ChatGPT stays unknown', async () => {
  const f = fixture()
  const result = await probe(f.ctx, { fetcher: f.fetcher })
  assert.equal(result.integrations[0].budget.reason, 'unreviewed-route')
  assert.equal(result.integrations[1].budget.status, 'partial')
  assert.equal(result.integrations[1].budget.observation.remaining, 75)
  assert.equal(result.integrations[1].budget.observation.budgets[0].id, 'shared-budget-stable')
  assert.equal(f.calls.resolve.length, 1)
  assert.equal(f.calls.resolve[0].id, 'cred_router')
  assert.equal(f.calls.fetch.length, 1)
  assert.equal(result.default_model, 'openrouter/openai/native')
  assert.ok(!JSON.stringify(result).includes(secret))
})

test('the production bridge dispatches a reviewed OpenAI OAuth route to the Codex reader', async () => {
  const f = fixture()
  f.providers[0].settings = { baseURL: 'https://chatgpt.com/backend-api/codex' }
  f.providers[0].headers = { 'chatgpt-account-id': 'synthetic-account' }
  const resolve = f.ctx.integration.connection.resolve
  f.ctx.integration.connection.resolve = async (connection) => connection.id === 'cred_openai'
    ? { type: 'oauth', methodID: 'chatgpt-headless', access: secret, refresh: secret,
        expires: Date.now() + 3600000, metadata: { accountID: 'synthetic-account' } }
    : resolve(connection)
  const result = await probe(f.ctx, { fetcher: f.fetcher,
    dependency: { executable: '/synthetic/codex', versions: ['0.162.1'] },
    openaiProbe: async (options) => {
      assert.equal(options.accessToken, secret)
      assert.equal(options.accountId, 'synthetic-account')
      assert.equal(options.refreshToken, undefined)
      return { authenticated_quota_verified: true, buckets: [{ limit_id: 'codex', window: 'primary',
        used_percent: 45, remaining_percent: 55, window_minutes: 10080, resets_at: 1900000000 }] }
    } })
  assert.equal(result.integrations[0].budget.status, 'ok')
  assert.equal(result.integrations[0].budget.observation.windows[0].remaining_percent, 55)
  assert.ok(!JSON.stringify(result).includes(secret))
})

test('a manifest allowlist can disable the remote budget probe', async () => {
  const f = fixture()
  const result = await probe(f.ctx, { fetcher: f.fetcher, rules: rules.filter((row) => row.service_id === 'openai') })
  assert.deepEqual(result.integrations.map((row) => row.id), ['openai'])
  assert.equal(f.calls.resolve.length, 0)
  assert.equal(f.calls.fetch.length, 0)
})

test('a new service is recognized from supplied rules without a secret reader', async () => {
  const f = fixture()
  f.providers.push({ id: 'example-route', integrationID: 'native-example', package: '@example/runtime', settings: {} })
  f.connections['native-example'] = { type: 'credential', id: 'example-account', method: 'key' }
  f.ctx.integration.list = async () => [{ id: 'native-example' }]
  const result = await probe(f.ctx, { fetcher: f.fetcher, rules: [{ id: 'native-example', service_id: 'example',
    packages: ['@example/runtime'], endpoints: ['https://example.test/v1'], authentication: [
      { method: 'key', budget_kind: 'api-billing', budget_adapter: 'disabled' },
    ] }] })
  assert.equal(result.integrations[0].budget.reason, 'reader-unavailable')
  assert.equal(result.integrations[0].active_connection.id, 'example-account')
  assert.equal(result.providers[2].endpoint_reviewed, true)
  assert.equal(f.calls.resolve.length, 0)
  assert.equal(f.calls.fetch.length, 0)
})

test('native integration aliases use the service reader rather than hardcoded IDs', async () => {
  const f = fixture()
  const router = rules.find((row) => row.service_id === 'openrouter')
  f.providers[1].integrationID = 'router-alias'
  f.connections['router-alias'] = f.connections.openrouter
  f.ctx.integration.list = async () => [{ id: 'router-alias' }]
  const result = await probe(f.ctx, { fetcher: f.fetcher, rules: [{ ...router, id: 'router-alias' }] })
  assert.equal(result.integrations[0].budget.status, 'partial')
  assert.equal(result.integrations[0].id, 'router-alias')
  assert.equal(f.calls.fetch.length, 1)
})

test('an empty catalog does not resolve credentials or fetch allowances', async () => {
  const f = fixture()
  const result = await probe(f.ctx, { fetcher: f.fetcher, rules: [] })
  assert.deepEqual(result.integrations, [])
  assert.equal(f.calls.resolve.length, 0)
  assert.equal(f.calls.fetch.length, 0)
})

test('OpenAI API-key mode does not reuse ChatGPT subscription allowance', async () => {
  const f = fixture(); f.connections.openai.method = 'key'
  const result = await probe(f.ctx, { fetcher: f.fetcher })
  assert.equal(result.integrations[0].budget.reason, 'api-billing-reader-unavailable')
  assert.equal(f.calls.resolve.length, 1)
})

test('a native environment connection uses its native resolver', async () => {
  const f = fixture(); f.connections.openrouter = { type: 'env', name: 'OPENROUTER_API_KEY' }
  const result = await probe(f.ctx, { fetcher: f.fetcher })
  assert.equal(result.integrations[1].active_connection.id, 'env:OPENROUTER_API_KEY')
  assert.equal(result.integrations[1].budget.status, 'partial')
})

test('unreviewed gateways, packages and credential overrides never resolve a secret', async () => {
  for (const change of [
    (p) => p.settings.baseURL = 'https://unreviewed.example/api/v1',
    (p) => p.settings.baseURL = 'https://openrouter.ai/api/v1?token=unsafe',
    (p) => p.package = '@opencode/ai/providers/openai-compatible',
    (p) => p.settings.openrouter = { baseURL: 'https://unreviewed.example' },
    (p) => p.settings.apiKey = secret,
    (p) => p.headers.Authorization = secret,
  ]) {
    const f = fixture(); change(f.providers[1])
    const result = await probe(f.ctx, { fetcher: f.fetcher })
    assert.equal(result.integrations[1].budget.reason, 'unreviewed-route')
    assert.equal(f.calls.resolve.length, 0)
    assert.equal(f.calls.fetch.length, 0)
    assert.ok(!JSON.stringify(result).includes(secret))
  }
})

test('no active connection and unsupported methods cannot trigger a budget request', async () => {
  for (const value of [undefined, { type: 'credential', method: 'oauth', id: 'cred_unknown' }]) {
    const f = fixture(); f.connections.openrouter = value
    const result = await probe(f.ctx, { fetcher: f.fetcher })
    assert.ok(['no-active-connection', 'unreviewed-method'].includes(result.integrations[1].budget.reason))
    assert.equal(f.calls.resolve.length, 0)
  }
})

test('a native account activation during a read discards the old quota', async () => {
  const f = fixture()
  const fetcher = async (...args) => {
    const response = await f.fetcher(...args)
    f.connections.openrouter = { id: 'cred_other', type: 'credential', method: 'key' }
    return response
  }
  const result = await probe(f.ctx, { fetcher })
  assert.equal(result.integrations[1].active_connection.id, 'cred_other')
  assert.deepEqual(result.integrations[1].budget, { status: 'unknown', reason: 'connection-changed' })
})

test('provider HTTP errors and exception text never cross the native boundary', async () => {
  for (const fetcher of [async () => new Response(secret, { status: 401 }),
    async () => { throw new Error(secret) }]) {
    const f = fixture()
    const result = await probe(f.ctx, { fetcher })
    assert.equal(result.integrations[1].budget.status, 'unavailable')
    assert.ok(!JSON.stringify(result).includes(secret))
  }
})

test('oversized or malformed native responses cannot become observations', async () => {
  for (const body of ['x'.repeat(300000), '{invalid', '{"data":{"limit":-1}}', '{"data":{"budgets":[null]}}']) {
    const f = fixture()
    const result = await probe(f.ctx, { fetcher: async () => new Response(body) })
    assert.equal(result.integrations[1].budget.reason, 'invalid-response')
    assert.ok(!('observation' in result.integrations[1].budget))
  }
})

test('an absent spending limit does not imply unlimited account credits', async () => {
  const f = fixture()
  const result = await probe(f.ctx, { fetcher: async () => Response.json({ data: { limit: null, limit_remaining: null, usage: 25 } }) })
  assert.equal(result.integrations[1].budget.reason, 'account-balance-unavailable')
  assert.equal(result.integrations[1].budget.observation.limit, null)
  assert.equal(result.integrations[1].budget.observation.remaining, null)
})

test('the observation parser rejects booleans, infinities and negative values', () => {
  for (const value of [true, Infinity, -1]) assert.throws(() => openrouterObservation({ limit: value }))
})

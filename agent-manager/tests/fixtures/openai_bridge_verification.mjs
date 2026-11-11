import assert from "node:assert/strict"
import test from "node:test"
import { codexProbe } from "../../agent_manager/integrations/native/codex.js"
import { inspectOpenAI, openaiBudget } from "../../agent_manager/integrations/native/openai.js"

const executable = process.argv[2]
const versions = ["0.162.1"]
const dependency = { executable, versions }
const inspect = (ctx, options = {}) => inspectOpenAI(ctx, { dependency, ...options })
const credential = { type: "oauth", methodID: "chatgpt-browser", access: "fixture-access-token",
  refresh: "fixture-refresh-secret", expires: Date.now() + 3600000, metadata: { accountID: "synthetic-account" } }
function fixture() {
  const selected = { type: "credential", id: "synthetic-native-id", method: "oauth" }
  const saved = { ...credential }
  const native = { id: "openai", integrationID: "openai", package: "@opencode/ai/providers/openai",
    settings: { baseURL: "https://chatgpt.com/backend-api/codex" },
    headers: { originator: "opencode", "chatgpt-account-id": "synthetic-account" } }
  const calls = []
  const ctx = { app: { version: "2.0.18" }, provider: { list: async () => [native] }, integration: { connection: {
    active: async () => selected, resolve: async () => { calls.push("resolve"); return { ...saved } },
  } } }
  return { ctx, native, selected, saved, calls }
}
const options = (accessToken = "fixture-access-token") => ({ executable, versions, accessToken, accountId: "synthetic-account" })

test("native handoff returns only quota fields and removes temporary credentials", async () => {
  const result = await codexProbe(options())
  assert.equal(result.status, "observed")
  assert.equal(result.authenticated_quota_verified, true)
  assert.equal(result.allowance_scope_verified, false)
  assert.equal(result.model_calls, 0)
  assert.equal(result.credential_persisted, false)
  assert.equal(result.temporary_home_removed, true)
  assert.equal(result.buckets[0].remaining_percent, 75)
  assert.equal(result.buckets[0].window_minutes, 10080)
  assert.ok(!JSON.stringify(result).includes("fixture-access-token"))
})

for (const [token, reason] of [["fixture-refresh-token", "refresh-required"], ["fixture-error-token", "rpc-rejected"],
    ["fixture-malformed-token", "invalid-response"], ["fixture-persist-token", "credential-persisted"]]) {
  test(`handoff rejects ${reason} without exposing native errors or tokens`, async () => {
    const result = await codexProbe(options(token))
    assert.equal(result.status, "failed")
    assert.equal(result.reason, reason)
    assert.equal(result.authenticated_quota_verified, false)
    assert.deepEqual(result.buckets, [])
    assert.ok(!JSON.stringify(result).includes(token))
  })
}

test("offline success qualifies local handoff and never authenticated quota", async () => {
  const result = await codexProbe({ ...options("fixture-error-token"), offline: true })
  assert.equal(result.status, "offline-handoff-verified")
  assert.equal(result.authenticated_quota_verified, false)
})

test("native resolver passes only the selected access token to the child", async () => {
  const f = fixture()
  let captured
  const result = await inspect(f.ctx, { executable, probe: async (args) => {
    captured = args
    return { status: "observed", authenticated_quota_verified: true, buckets: [] }
  } })
  assert.equal(captured.accessToken, credential.access)
  assert.equal(captured.accountId, credential.metadata.accountID)
  assert.ok(!JSON.stringify(captured).includes(credential.refresh))
  assert.equal(result.auth_method, "chatgpt-browser")
  assert.equal(result.allowance_scope_verified, false)
  assert.ok(!JSON.stringify(result).includes(credential.access))
})

test("account changes discard the observation", async () => {
  const f = fixture()
  const result = await inspect(f.ctx, { executable, probe: async () => {
    f.ctx.integration.connection.active = async () => ({ ...f.selected, id: "other-account" })
    return { status: "observed", authenticated_quota_verified: true, buckets: [{ remaining_percent: 75 }] }
  } })
  assert.equal(result.reason, "connection-changed")
  assert.equal(result.authenticated_quota_verified, false)
  assert.equal(result.buckets, undefined)
})

test("rewriting a credential under the same native ID also discards the observation", async () => {
  const f = fixture()
  const result = await inspect(f.ctx, { probe: async () => {
    f.saved.access = "other-account-token"
    return { status: "observed", authenticated_quota_verified: true, buckets: [{ limit_id: "codex", remaining_percent: 75 }] }
  } })
  assert.equal(result.reason, "connection-changed")
  assert.equal(result.authenticated_quota_verified, false)
})

for (const [name, mutate] of [
  ["API key", (f) => { f.selected.method = "key" }],
  ["custom endpoint", (f) => { f.native.settings.baseURL = "https://example.invalid" }],
  ["nested override", (f) => { f.native.settings.openai = { baseURL: "https://example.invalid" } }],
  ["custom authorization", (f) => { f.native.headers.Authorization = "fixture-secret" }],
]) {
  test(`${name} never resolves credentials or starts a Codex child`, async () => {
    const f = fixture(); mutate(f)
    const result = await inspect(f.ctx, { probe: async () => { throw Error("Must not run") } })
    assert.equal(result.status, "unsupported")
    assert.deepEqual(f.calls, [])
  })
}

test("an unreviewed OAuth method is never passed to Codex", async () => {
  const f = fixture(); f.saved.methodID = "sign-in-with-chatgpt"
  const result = await inspect(f.ctx, { probe: async () => { throw Error("Must not run") } })
  assert.equal(result.reason, "unreviewed-method")
  assert.deepEqual(f.calls, ["resolve"])
})

test("the native headless method uses the same access-token handoff", async () => {
  const f = fixture(); f.saved.methodID = "chatgpt-headless"
  const result = await inspect(f.ctx, { executable, probe: async () => ({ status: "observed", authenticated_quota_verified: true }) })
  assert.equal(result.auth_method, "chatgpt-headless")
})

test("a workspace header different from the selected credential never reaches Codex", async () => {
  const f = fixture(); f.native.headers["chatgpt-account-id"] = "other-workspace"
  const result = await inspect(f.ctx, { probe: async () => { throw Error("Must not run") } })
  assert.equal(result.reason, "unreviewed-route")
  assert.equal(result.authenticated_quota_verified, false)
})

test("a missing login returns a fixed explanation", async () => {
  const f = fixture(); f.ctx.integration.connection.active = async () => null
  const result = await inspect(f.ctx)
  assert.equal(result.status, "login-required")
  assert.deepEqual(f.calls, [])
})

test("a native resolution error is not returned to Manager", async () => {
  const f = fixture(); f.ctx.integration.connection.resolve = async () => { throw Error(credential.refresh) }
  const result = await inspect(f.ctx)
  assert.equal(result.reason, "authentication")
  assert.ok(!JSON.stringify(result).includes(credential.refresh))
})

test("production reader normalizes the verified legacy route without credentials in its result", async () => {
  const f = fixture()
  const result = await openaiBudget(f.ctx, { dependency })
  assert.equal(result.status, "ok")
  assert.equal(result.reason, "chatgpt-codex-allowance")
  assert.equal(result.observation.windows[0].remaining_percent, 75)
  assert.ok(!JSON.stringify(result).includes(credential.access))
  assert.ok(!JSON.stringify(result).includes(credential.refresh))
})

test("missing Codex is reported before any credential resolution", async () => {
  const f = fixture()
  const result = await openaiBudget(f.ctx, { dependency: null })
  assert.equal(result.reason, "missing-cli")
  assert.deepEqual(f.calls, [])
})

test("an unsupported Codex version never receives the token", async () => {
  const result = await codexProbe({ ...options(), versions: [] })
  assert.equal(result.reason, "unsupported-codex-version")
  assert.equal(result.external_login_accepted, false)
})

test("cancellation closes the isolated child without credentials in public errors", async () => {
  const controller = new AbortController(); controller.abort()
  const result = await codexProbe({ ...options(), signal: controller.signal })
  assert.equal(result.reason, "timeout")
  assert.equal(result.temporary_home_removed, true)
})

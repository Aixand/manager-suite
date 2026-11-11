import { codexProbe } from "./codex.js"

const methods = new Set(["chatgpt-browser", "chatgpt-headless"])
const list = (value) => Array.isArray(value) ? value : value?.data

export function configuredCodex() {
  try {
    const value = JSON.parse(process.env.AGENT_MANAGER_OPENAI_CODEX ?? "null")
    if (!value || (value.executable !== null && typeof value.executable !== "string") || !Array.isArray(value.versions)
        || value.versions.length > 64 || value.versions.some((v) => typeof v !== "string" || !/^\d+\.\d+\.\d+$/.test(v))) return null
    return value
  } catch { return null }
}

export async function inspectOpenAI(ctx, { connection, providers, dependency = configuredCodex(), signal, probe = codexProbe } = {}) {
  const result = { status: "unsupported", authenticated_quota_verified: false,
    allowance_scope_verified: false, model_calls: 0 }
  try {
    connection ??= await ctx.integration.connection.active("openai")
    if (!connection) return { ...result, status: "login-required", reason: "no-active-connection" }
    if (connection.type !== "credential" || connection.method !== "oauth") return { ...result, reason: "unreviewed-method" }
    const native = list(providers ?? await ctx.provider.list())?.find((row) => row.id === "openai")
    if (!native || native.integrationID !== "openai" || native.package !== "@opencode/ai/providers/openai"
        || native.activation === "disabled" || native.settings?.baseURL?.replace(/\/$/, "") !== "https://chatgpt.com/backend-api/codex"
        || Object.values(native.settings ?? {}).some((value) => value !== null && typeof value === "object")
        || Object.keys(native.settings ?? {}).some((key) => /key|token|secret|authorization/i.test(key))
        || Object.keys(native.headers ?? {}).some((key) => /authorization|api.key|cookie/i.test(key))) {
      return { ...result, reason: "unreviewed-route" }
    }
    if (!dependency?.executable) return { ...result, status: "failed", reason: "missing-cli" }
    if (!dependency.versions?.length) return { ...result, reason: "unsupported-codex-version" }
    const credential = await ctx.integration.connection.resolve(connection)
    if (credential?.type !== "oauth" || !methods.has(credential.methodID)) return { ...result, reason: "unreviewed-method" }
    if (!Number.isFinite(credential.expires) || credential.expires <= Date.now()) {
      return { ...result, status: "failed", reason: "authentication" }
    }
    const headers = Object.entries(native.headers ?? {}).filter(([name]) => name.toLowerCase() === "chatgpt-account-id")
    if (headers.length !== 1 || headers[0][1] !== credential.metadata?.accountID) return { ...result, reason: "unreviewed-route" }
    const observation = await probe({ executable: dependency.executable, versions: dependency.versions,
      accessToken: credential.access, accountId: credential.metadata?.accountID, signal, timeout: 8000 })
    const current = await ctx.integration.connection.active("openai")
    if (current?.type !== connection.type || current?.id !== connection.id || current?.method !== connection.method) {
      return { ...result, status: "failed", reason: "connection-changed" }
    }
    const latest = await ctx.integration.connection.resolve(current)
    if (latest?.access !== credential.access || latest?.methodID !== credential.methodID
        || latest?.metadata?.accountID !== credential.metadata?.accountID) {
      return { ...result, status: "failed", reason: "connection-changed" }
    }
    // The reviewed OAuth methods route inference to Codex services with this
    // same credential/account header. Only their general Codex meter is ranked;
    // unfamiliar metering groups remain observations without a ranking claim.
    const qualified = observation.authenticated_quota_verified && observation.buckets?.length > 0
      && observation.buckets.every((row) => row.limit_id === "codex")
    return { ...result, ...observation, allowance_scope_verified: Boolean(qualified),
      auth_method: credential.methodID, source: "codex-app-server" }
  } catch { return { ...result, status: "failed", reason: "authentication" } }
}

export async function openaiBudget(ctx, options) {
  const report = await inspectOpenAI(ctx, options)
  if (report.authenticated_quota_verified) return { status: report.buckets.some((row) => row.remaining_percent != null)
      ? report.buckets.every((row) => row.remaining_percent != null) ? "ok" : "partial" : "unknown",
    reason: report.buckets.length ? "chatgpt-codex-allowance" : "chatgpt-no-allowance",
    observation: { windows: report.buckets } }
  const reasons = { "rpc-rejected": "unavailable", "refresh-required": "authentication", "invalid-credential": "authentication",
    "credential-persisted": "invalid-response", "unexpected-server-request": "invalid-response" }
  const reason = reasons[report.reason] ?? report.reason ?? "unavailable"
  return { status: report.status === "unsupported" ? "unsupported" : "unavailable", reason }
}

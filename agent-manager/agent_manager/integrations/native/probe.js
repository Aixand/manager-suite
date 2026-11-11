// Runs only inside OpenCode. Never returns credentials, endpoints or native errors.
// Public contract: agent-manager/opencode-bridge-2, OpenCode 2.0.18.
import { configuredCodex, openaiBudget } from "./openai.js"
const list = (value) => Array.isArray(value) ? value : value?.data
const publicConnection = (value) => !value ? null : value.type === "credential"
  ? { id: value.id, type: "credential", method: value.method }
  : value.type === "env" ? { id: `env:${value.name}`, type: "env", method: "key" } : null

function reviewedEndpoint(provider, rule) {
  if (!rule || !rule.packages.includes(provider.package)) return false
  const settings = provider.settings ?? {}
  if (provider.activation === "disabled" || Object.keys(settings).some((key) => /key|token|secret|authorization/i.test(key))
    || Object.keys(provider.headers ?? {}).some((key) => /authorization|api.key|cookie/i.test(key))) return false
  // Nested package-specific settings can override a root base URL. Their shape
  // has not been reviewed, so never resolve a secret for those configurations.
  if (Object.values(settings).some((value) => value !== null && typeof value === "object")) return false
  const endpoint = settings.baseURL
  return endpoint === undefined || typeof endpoint === "string" && rule.endpoints.includes(endpoint.replace(/\/$/, ""))
}

function numeric(value) {
  if (value === null || value === undefined) return null
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) throw new Error("invalid-response")
  return value
}

export function openrouterObservation(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid-response")
  const budgets = value.budgets
  if (budgets !== undefined && (!Array.isArray(budgets) || budgets.length > 64)) throw new Error("invalid-response")
  return {
    limit: numeric(value.limit), remaining: numeric(value.limit_remaining), used: numeric(value.usage),
    reset_interval: ["daily", "weekly", "monthly"].includes(value.limit_reset) ? value.limit_reset : null,
    budgets: (budgets ?? []).map((row, index) => {
      if (!row || typeof row !== "object" || Array.isArray(row)) throw new Error("invalid-response")
      const id = row.budget_id ?? row.guardrail_id
      return {
        id: `shared-${typeof id === "string" && /^[A-Za-z0-9_.:-]{1,128}$/.test(id) ? id : index}`,
        scope: ["member", "workspace", "organization", "key"].includes(row.scope) ? row.scope : "shared",
        limit: numeric(row.limit_usd), remaining: numeric(row.remaining_usd), used: numeric(row.spend_usd),
        resets_at: typeof row.resets_at === "string" && Number.isFinite(Date.parse(row.resets_at)) ? row.resets_at : null,
      }
    }),
  }
}

async function quota(ctx, rule, connection, signal, fetcher, openai) {
  if (!connection) return { status: "unknown", reason: "no-active-connection" }
  const method = connection.type === "env" ? "key" : connection.method
  const auth = rule.authentication.find((row) => row.method === method)
  if (!auth) return { status: "unsupported", reason: "unreviewed-method" }
  if (auth.budget_adapter === "disabled") return { status: "unknown", reason: "reader-unavailable" }
  if (auth.budget_adapter === "openai-native" && rule.service_id === "openai" && ["key", "oauth"].includes(method)) {
    return method === "oauth" ? openai() : { status: "unknown", reason: "api-billing-reader-unavailable" }
  }
  // Reader endpoints and credential protocols remain fixed in their adapters.
  // A manifest endpoint is a routing constraint, never an outbound quota URL.
  if (auth.budget_adapter !== "openrouter-key" || rule.service_id !== "openrouter" || method !== "key") {
    return { status: "unsupported", reason: "unreviewed-method" }
  }
  try {
    // Use OpenCode's active-connection resolver, not the HTTP credential list or
    // its SQLite store. The credential stays in this native process's memory.
    const credential = await ctx.integration.connection.resolve(connection)
    if (!credential || credential.type !== "key" || typeof credential.key !== "string" || !credential.key) {
      return { status: "unavailable", reason: "authentication" }
    }
    const timeout = AbortSignal.timeout(8000)
    const combined = signal ? AbortSignal.any([signal, timeout]) : timeout
    const response = await fetcher("https://openrouter.ai/api/v1/key", {
      headers: { Authorization: `Bearer ${credential.key}` }, redirect: "error", signal: combined,
    })
    if (!response.ok) return { status: "unavailable", reason: [401, 403].includes(response.status) ? "authentication" : "unavailable" }
    // Limit the body while reading; do not retain headers or failure bodies.
    const reader = response.body?.getReader()
    if (!reader) return { status: "unavailable", reason: "invalid-response" }
    const chunks = []; let size = 0
    try {
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        size += value.byteLength
        if (size > 262144) throw new Error("invalid-response")
        chunks.push(value)
      }
    } finally { await reader.cancel().catch(() => {}) }
    const bytes = new Uint8Array(size); let offset = 0
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
    const value = JSON.parse(new TextDecoder().decode(bytes))
    const observation = openrouterObservation(value.data)
    // A key without a spending cap does not establish an unlimited wallet.
    return { status: "partial", reason: "account-balance-unavailable", observation }
  } catch (error) {
    return { status: "unavailable", reason: error?.message === "invalid-response" || error instanceof SyntaxError
      ? "invalid-response" : error?.name === "TimeoutError" || signal?.aborted ? "timeout" : "unavailable" }
  }
}

export async function inspect(ctx, { signal, fetcher = globalThis.fetch, rules = [], dependency = configuredCodex(), openaiProbe } = {}) {
  const nativeProviders = list(await ctx.provider.list())
  const nativeIntegrations = list(await ctx.integration.list())
  if (!Array.isArray(nativeProviders) || !Array.isArray(nativeIntegrations)) throw new Error("invalid-response")
  const providers = nativeProviders.map((row) => ({
    id: row.id, integration_id: row.integrationID ?? null, package: row.package,
    endpoint_reviewed: reviewedEndpoint(row, rules.find((rule) => rule.id === row.integrationID)),
  }))
  const integrations = []
  for (const rule of rules) {
    const integrationID = rule.id
    if (!nativeIntegrations.some((row) => row.id === integrationID)) continue
    const connection = await ctx.integration.connection.active(integrationID)
    const reviewed = providers.some((row) => row.integration_id === integrationID && row.endpoint_reviewed)
    integrations.push({ id: integrationID, active_connection: publicConnection(connection),
      budget: reviewed ? await quota(ctx, rule, connection, signal, fetcher,
        () => openaiBudget(ctx, { connection, providers: nativeProviders, dependency, signal, probe: openaiProbe }))
        : { status: "unsupported", reason: "unreviewed-route" } })
  }
  // Refuse a result if a native account was activated while the quota query ran.
  for (const row of integrations) {
    const current = publicConnection(await ctx.integration.connection.active(row.id))
    if (JSON.stringify(current) !== JSON.stringify(row.active_connection)) {
      row.active_connection = current
      row.budget = { status: "unknown", reason: "connection-changed" }
    }
  }
  const selected = await ctx.model.default()
  const model = selected?.data ?? selected
  return { schema: "agent-manager/opencode-bridge-2", version: ctx.app.version, providers, integrations,
    default_model: model?.providerID && model?.modelID ? `${model.providerID}/${model.modelID}` : null }
}

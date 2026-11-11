// Bounded Codex quota transport. Only an access token enters the isolated child;
// OpenCode retains ownership of the login and its refresh token.
import { spawn } from "node:child_process"
import { mkdtemp, mkdir, readdir, readFile, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join, isAbsolute } from "node:path"

const MAX_OUTPUT = 1024 * 1024
const error = (code) => Object.assign(new Error(code), { code })

function limits(value) {
  const groups = value.rateLimitsByLimitId ?? (value.rateLimits ? {
    [value.rateLimits.limitId ?? "default"]: value.rateLimits,
  } : {})
  if (!groups || typeof groups !== "object" || Array.isArray(groups)) throw error("invalid-response")
  const result = []
  for (const [id, group] of Object.entries(groups)) {
    if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(id) || !group || typeof group !== "object" || Array.isArray(group)) throw error("invalid-response")
    for (const window of ["primary", "secondary"]) {
      const row = group[window]
      if (row == null) continue
      if (!row || typeof row !== "object" || Array.isArray(row) || row.usedPercent != null && (!Number.isFinite(row.usedPercent)
          || row.usedPercent < 0 || row.usedPercent > 100)) throw error("invalid-response")
      for (const key of ["windowDurationMins", "resetsAt"]) {
        if (row[key] != null && (!Number.isFinite(row[key]) || row[key] < 0)) throw error("invalid-response")
      }
      if (result.length >= 128) throw error("invalid-response")
      result.push({ limit_id: id, window, used_percent: row.usedPercent ?? null,
        remaining_percent: row.usedPercent == null ? null : 100 - row.usedPercent,
        window_minutes: row.windowDurationMins ?? null, resets_at: row.resetsAt ?? null })
    }
  }
  return result
}

async function storedCredential(directory, token) {
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, item.name)
    if (item.isSymbolicLink()) throw error("unexpected-file")
    if (item.isDirectory()) { if (await storedCredential(path, token)) return true }
    else if (item.isFile()) {
      if (item.name === "auth.json" || (await readFile(path)).includes(Buffer.from(token))) return true
    }
  }
  return false
}

export async function codexProbe({ executable, versions = [], accessToken, accountId, signal, offline = false, timeout = 8000 }) {
  if (typeof executable !== "string" || !isAbsolute(executable)) throw error("missing-cli")
  if (typeof accessToken !== "string" || !accessToken
      || accessToken.length > 65536 || typeof accountId !== "string" || !/^[A-Za-z0-9_-]{1,256}$/.test(accountId)) {
    throw error("invalid-credential")
  }
  const directory = await mkdtemp(join(tmpdir(), "agent-openai-quota-"))
  let child, timer, settled, failed, abort, credentialStored = false
  const pending = new Map()
  const report = { external_login_accepted: false, authenticated_quota_verified: false,
    allowance_scope_verified: false, model_calls: 0, buckets: [] }
  try {
    const home = join(directory, "codex")
    await mkdir(home, { mode: 0o700 })
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => [
      "PATH", "LANG", "SYSTEMROOT", "SystemRoot", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
      "http_proxy", "https_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
    ].includes(key)))
    Object.assign(env, { HOME: directory, USERPROFILE: directory, CODEX_HOME: home,
      XDG_CONFIG_HOME: join(directory, "config"), XDG_DATA_HOME: join(directory, "data"),
      XDG_STATE_HOME: join(directory, "state"), XDG_CACHE_HOME: join(directory, "cache"),
      TMPDIR: directory, TEMP: directory, TMP: directory, RUST_LOG: "off" })
    if (offline) {
      for (const key of ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]) env[key] = "http://127.0.0.1:9"
      env.NO_PROXY = env.no_proxy = ""
    }
    const args = ["app-server", "--listen", "stdio://", "-c", 'cli_auth_credentials_store="ephemeral"',
      "-c", "analytics.enabled=false", "-c", "feedback.enabled=false", "-c", "check_for_update_on_startup=false"]
    if (offline) args.push("-c", 'chatgpt_base_url="http://127.0.0.1:9"')
    child = spawn(executable, args, { cwd: directory, env, stdio: ["pipe", "pipe", "ignore"] })
    settled = new Promise((resolve) => child.once("close", resolve))
    const fail = (code) => {
      failed ??= code
      for (const { reject } of pending.values()) reject(error(failed))
      pending.clear()
    }
    abort = () => fail("timeout")
    signal?.addEventListener("abort", abort, { once: true })
    if (signal?.aborted) abort()
    child.on("error", () => fail("unavailable"))
    child.stdin.on("error", () => fail("unavailable"))
    child.on("close", () => fail("unavailable"))
    let buffer = "", received = 0, counter = 0
    child.stdout.setEncoding("utf8")
    child.stdout.on("data", (chunk) => {
      if (failed) return
      received += Buffer.byteLength(chunk)
      if (received > MAX_OUTPUT) { fail("invalid-response"); return }
      buffer += chunk
      let newline
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline); buffer = buffer.slice(newline + 1)
        try {
          const item = JSON.parse(line)
          if (!item || typeof item !== "object" || Array.isArray(item)) throw error("invalid-response")
          if (item.method) {
            // OpenCode remains the only refresh owner. Do not request, transfer
            // or independently redeem its refresh token for this probe.
            if (Object.hasOwn(item, "id")) { fail(item.method === "account/chatgptAuthTokens/refresh" ? "refresh-required" : "unexpected-server-request"); return }
            continue
          }
          const waiting = pending.get(item.id)
          if (!waiting) continue
          pending.delete(item.id)
          if (item.error) waiting.reject(error("rpc-rejected"))
          else if (!item.result || typeof item.result !== "object" || Array.isArray(item.result)) waiting.reject(error("invalid-response"))
          else waiting.resolve(item.result)
        } catch { fail("invalid-response"); return }
      }
    })
    timer = setTimeout(() => fail("timeout"), timeout)
    const request = (method, params) => new Promise((resolve, reject) => {
      if (failed) { reject(error(failed)); return }
      const id = ++counter
      pending.set(id, { resolve, reject })
      child.stdin.write(JSON.stringify({ id, method, params }) + "\n")
    })
    const initialized = await request("initialize", {
      clientInfo: { name: "agent_manager_budget", version: "1.0.0" }, capabilities: { experimentalApi: true },
    })
    const version = initialized.userAgent?.match(/\/(\d+\.\d+\.\d+)(?:\s|$)/)?.[1]
    if (!versions.includes(version)) throw error("unsupported-codex-version")
    report.codex_version = version
    child.stdin.write('{"method":"initialized","params":{}}\n')
    const login = await request("account/login/start", { type: "chatgptAuthTokens", accessToken, chatgptAccountId: accountId })
    if (login.type !== "chatgptAuthTokens") throw error("invalid-response")
    report.external_login_accepted = true
    const quota = await request("account/rateLimits/read", {})
    report.buckets = limits(quota)
    report.authenticated_quota_verified = !offline
    report.status = report.buckets.length ? "observed" : "no-allowance-returned"
  } catch (failure) {
    report.status = offline && report.external_login_accepted && failure.code === "rpc-rejected"
      ? "offline-handoff-verified" : "failed"
    report.reason = ["invalid-credential", "unavailable", "invalid-response", "refresh-required", "unexpected-server-request",
      "rpc-rejected", "timeout", "unsupported-codex-version"].includes(failure.code) ? failure.code : "unavailable"
  } finally {
    clearTimeout(timer)
    if (abort) signal?.removeEventListener("abort", abort)
    if (child) {
      child.stdin.destroy()
      child.kill()
      let killTimer
      try {
        await Promise.race([settled, new Promise((resolve) => { killTimer = setTimeout(() => { child.kill("SIGKILL"); resolve() }, 1000) })])
      } finally { clearTimeout(killTimer) }
    }
    try { credentialStored = await storedCredential(directory, accessToken) }
    catch { credentialStored = true }
    await rm(directory, { recursive: true, force: true })
  }
  report.credential_persisted = credentialStored
  report.temporary_home_removed = true
  if (credentialStored) {
    report.status = "failed"; report.reason = "credential-persisted"
    report.authenticated_quota_verified = false; report.buckets = []
  }
  return report
}

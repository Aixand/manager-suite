import { codexProbe } from "../../../agent_manager/integrations/native/codex.js"

const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url")
const token = [encode({ alg: "RS256", typ: "JWT" }), encode({ sub: "agent-manager-synthetic-user",
  email: "synthetic@example.invalid", exp: Math.floor(Date.now() / 1000) + 3600,
  "https://api.openai.com/auth": { chatgpt_account_id: "agent-manager-synthetic-account",
    chatgpt_plan_type: "plus", chatgpt_user_id: "agent-manager-synthetic-user" },
}), Buffer.from("synthetic signature").toString("base64url")].join(".")

const result = await codexProbe({ executable: process.argv[2], versions: JSON.parse(process.argv[3]), accessToken: token,
  accountId: "agent-manager-synthetic-account", offline: true })
console.log(JSON.stringify(result))
process.exitCode = result.status === "offline-handoff-verified" ? 0 : 1

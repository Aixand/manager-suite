import { BudgetProbe } from "../../../agent_manager/integrations/native/rpc.js"
import { inspectOpenAI } from "../../../agent_manager/integrations/native/openai.js"

export async function inspect(ctx) {
  const dependency = JSON.parse(process.env.AGENT_MANAGER_OPENAI_CODEX ?? "null")
  return { schema: "agent-manager/openai-bridge-verification-1", opencode_version: ctx.app.version,
    ...await inspectOpenAI(ctx, { dependency }) }
}

export default {
  id: "aixand.agent.openai-bridge-verification",
  async setup(ctx) {
    const registration = await ctx.rpc.register(BudgetProbe, { read: async (input) => {
      if (input.rules.length) throw new Error("Verification accepts no discovery rules")
      return inspect(ctx)
    } })
    return () => registration.dispose()
  },
}

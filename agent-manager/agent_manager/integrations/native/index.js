import { BudgetProbe } from "./rpc.js"
import { inspect } from "./probe.js"

const identifier = /^[a-z0-9][a-z0-9_-]{0,127}$/

// The Promise plugin runtime contract is { id, setup }. Plugin.define is a
// type helper that returns this object unchanged. Keep this shipped directory
// self-contained: OpenCode does not supply server-side bare SDK imports.
export default {
  id: "aixand.agent.budget-probe",
  async setup(ctx) {
    const registration = await ctx.rpc.register(BudgetProbe, {
      read: async (input, context) => {
        if (input.rules.some((rule) => !identifier.test(rule.id) || !identifier.test(rule.service_id))) {
          throw new Error("Invalid service discovery identifier")
        }
        return inspect(ctx, { signal: context.signal, rules: input.rules })
      },
    })
    return () => registration.dispose()
  },
}

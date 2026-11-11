// Portable JSON-Schema RPC definition; no runtime SDK dependency. The native
// registry reads events even when there are no events to expose.
export const BudgetProbe = {
  id: "aixand.agent.budgets.v2",
  events: {},
  methods: {
    read: {
      input: { type: "object", properties: { rules: { type: "array", maxItems: 128, items: {
        type: "object", additionalProperties: false,
        required: ["id", "service_id", "packages", "endpoints", "authentication"], properties: {
          // OpenCode 2.0.18's JSON-Schema conversion rejects pattern keywords.
          // Keep identifier validation in the handler, after structural parsing.
          id: { type: "string", minLength: 1, maxLength: 128 },
          service_id: { type: "string", minLength: 1, maxLength: 128 },
          packages: { type: "array", minItems: 1, maxItems: 64, uniqueItems: true, items: { type: "string", maxLength: 256 } },
          endpoints: { type: "array", minItems: 1, maxItems: 64, uniqueItems: true, items: { type: "string", maxLength: 512 } },
          authentication: { type: "array", minItems: 1, maxItems: 4, items: { type: "object", additionalProperties: false,
            required: ["method", "budget_kind", "budget_adapter"], properties: {
              method: { enum: ["key", "oauth", "external", "command"] },
              budget_kind: { enum: ["subscription", "api-billing", "credits", "unknown"] },
              budget_adapter: { enum: ["disabled", "openai-native", "openrouter-key"] },
            } } },
        },
      } } }, required: ["rules"], additionalProperties: false },
      output: { type: "object", additionalProperties: true },
    },
  },
}

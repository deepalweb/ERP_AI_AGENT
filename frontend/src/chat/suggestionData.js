// Static example questions, grouped to match what the agent can actually
// answer today (not a generic chatbot starter list) — each tagged with the
// tool it's expected to trigger, so usage history can personalize suggestions
// without needing a separate backend.

export const CATEGORIES = [
  {
    id: "business",
    label: "Business",
    emoji: "📊",
    questions: [
      { text: "What needs my attention right now?", tool: "scan_attention" },
      { text: "Give me an executive summary", tool: "scan_attention" },
      { text: "Are we too dependent on a few customers?", tool: "get_revenue_concentration" },
    ],
  },
  {
    id: "sales",
    label: "Sales",
    emoji: "💰",
    questions: [
      { text: "How were sales last month?", tool: "get_sales_summary" },
      { text: "Which products sell the most?", tool: "get_top_products" },
      { text: "Is revenue growth coming from more orders or higher prices?", tool: "average_order_value" },
      { text: "How much comes through PickMe vs Uber?", tool: "get_channel_breakdown" },
    ],
  },
  {
    id: "customers",
    label: "Customers",
    emoji: "👥",
    questions: [
      { text: "Which customers are at risk of churning?", tool: "get_customer_intelligence" },
      { text: "What % of revenue comes from our top customers?", tool: "get_revenue_concentration" },
    ],
  },
  {
    id: "inventory",
    label: "Inventory",
    emoji: "📦",
    questions: [
      { text: "What's out of stock right now?", tool: "get_stock_alerts" },
      { text: "Which products are dead stock?", tool: "get_inventory_depth" },
      { text: "Which outlet has the most inventory?", tool: "get_inventory_by_outlet" },
    ],
  },
  {
    id: "operations",
    label: "Operations",
    emoji: "🚚",
    questions: [
      { text: "How are deliveries performing?", tool: "get_operations_summary" },
      { text: "Which branch needs attention?", tool: "get_operations_summary" },
    ],
  },
  {
    id: "investigate",
    label: "Investigate",
    emoji: "🔍",
    questions: [
      { text: "Why did revenue decline?", tool: "investigate_metric:revenue" },
      { text: "Investigate delivery delays", tool: "investigate_metric:delayed_delivery_rate" },
      { text: "Why did gross margin change?", tool: "investigate_metric:gross_margin" },
      { text: "Is this a temporary problem or a trend?", tool: "investigate_metric:revenue" },
    ],
  },
];

const ALL_QUESTIONS = CATEGORIES.flatMap((c) => c.questions);

// Frequently-asked chips personalize by usage — count how often each tool
// has actually been called across stored conversations, then surface the
// example question for the most-used tools first. Purely client-side: no
// backend, derived entirely from the chat history already in localStorage.
export function getFrequentQuestions(conversations, limit = 4) {
  const counts = {};
  for (const convo of conversations || []) {
    for (const message of convo.messages || []) {
      for (const call of message.toolCalls || []) {
        // investigate_metric's tool name is always the same — the metric it
        // investigated is an argument, not the name — so key on that instead
        // or usage of individual metrics collapses into one bucket.
        const key =
          call.name === "investigate_metric" && call.arguments?.metric_id
            ? `investigate_metric:${call.arguments.metric_id}`
            : call.name;
        counts[key] = (counts[key] || 0) + 1;
      }
    }
  }

  const usedTools = Object.keys(counts);
  if (usedTools.length === 0) return [];

  const seen = new Set();
  const ranked = [];
  for (const tool of usedTools.sort((a, b) => counts[b] - counts[a])) {
    const question = ALL_QUESTIONS.find((q) => q.tool === tool && !seen.has(q.text));
    if (question) {
      seen.add(question.text);
      ranked.push(question);
    }
    if (ranked.length >= limit) break;
  }
  return ranked;
}

// Two tiers for v1: "confirmed" for anything computed directly from the
// database (anomaly detection, breakdowns), "suggested" for LLM-generated
// recommendation text. A real multi-tier confidence scale (possible/
// insufficient-evidence) needs the backend to tag its own claims, which it
// doesn't do yet — see the plan. Don't invent finer-grained tiers client-side
// than the data actually supports.
const TIERS = {
  confirmed: {
    label: "Confirmed",
    className: "bg-good/15 text-good border-good/30",
  },
  suggested: {
    label: "Suggested",
    className: "bg-accent-soft text-accent border-accent-border",
  },
};

export default function ConfidenceBadge({ tier = "confirmed", className = "" }) {
  const meta = TIERS[tier] ?? TIERS.confirmed;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${meta.className} ${className}`}
    >
      {meta.label}
    </span>
  );
}

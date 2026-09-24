import Badge from "./Badge";

// Two tiers for v1: "confirmed" for anything computed directly from the
// database (anomaly detection, breakdowns), "suggested" for LLM-generated
// recommendation text. A real multi-tier confidence scale (possible/
// insufficient-evidence) needs the backend to tag its own claims, which it
// doesn't do yet. Don't invent finer-grained tiers client-side than the
// data actually supports.
const TIER_TONE = { confirmed: "good", suggested: "accent" };
const TIER_LABEL = { confirmed: "Confirmed", suggested: "Suggested" };

export default function ConfidenceBadge({ tier = "confirmed", className = "" }) {
  return (
    <Badge tone={TIER_TONE[tier] ?? TIER_TONE.confirmed} className={className}>
      {TIER_LABEL[tier] ?? TIER_LABEL.confirmed}
    </Badge>
  );
}

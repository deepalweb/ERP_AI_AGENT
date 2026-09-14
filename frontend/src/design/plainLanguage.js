/** One deterministic, plain-English sentence per finding — same "stated
 * rule, not a judgment call" posture as investigation.py's _priority_for
 * and impact_engine.py's business_impact_score. Built entirely from fields the
 * backend already computes (priority, persistence pattern/consecutive
 * periods, the weekly streak, confirm.pct_change) — never invented text,
 * never an LLM call. The goal is a sentence a manager can read without
 * first decoding a badge color and a percentage: "is this good or bad,
 * how long has it been going on, how urgent is it."
 */

const URGENCY = {
  critical: "needs attention now",
  high: "needs attention soon",
  medium: "worth keeping an eye on",
};

export function headlineFor(finding) {
  const { category, priority, persistence, confirm, horizon, streak } = finding;

  if (category === "opportunity") {
    if (persistence?.pattern?.startsWith("consistent_improving") && persistence.consecutive_periods >= 2) {
      const unit = horizon === "weekly" ? "weeks" : "months";
      return `Good news — this has kept improving for ${persistence.consecutive_periods} ${unit} in a row.`;
    }
    if (persistence?.pattern === "recovering") {
      return "Good news — this is bouncing back after a rough patch.";
    }
    return "Good news — this is trending in the right direction.";
  }

  const urgency = URGENCY[priority] ?? "worth keeping an eye on";

  if (horizon === "weekly" && streak?.consecutive_days > 1) {
    return `This has been a problem for ${streak.consecutive_days} days straight — ${urgency}.`;
  }

  if (persistence?.pattern === "recovering") {
    return `This was a problem, but it's turning around — still worth confirming it holds.`;
  }

  if (persistence?.pattern?.startsWith("consistent_declining") && persistence.consecutive_periods >= 2) {
    const unit = horizon === "weekly" ? "weeks" : "months";
    return `This has been getting worse for ${persistence.consecutive_periods} ${unit} in a row — ${urgency}.`;
  }

  if (confirm?.pct_change == null) {
    return `This is a real problem right now — ${urgency}.`;
  }

  return `This just showed up — ${urgency}.`;
}

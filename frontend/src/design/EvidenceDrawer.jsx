import { useState } from "react";

/** Lightweight, inline "why do you think that?" panel — an alternative to
 * jumping to the full Investigation stepper when a Signal Card's claim just
 * needs a one-line justification, not a full drill-in. */
export default function EvidenceDrawer({ trigger = "Why?", children }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="text-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="text-accent hover:underline font-medium"
      >
        {trigger} {open ? "−" : "+"}
      </button>
      {open && (
        <div className="mt-2 rounded-lg border border-border bg-background p-3">
          {children}
        </div>
      )}
    </div>
  );
}

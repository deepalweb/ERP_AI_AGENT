import { useState } from "react";

/** Lightweight, inline "why do you think that?" panel -- a smaller-weight
 * alternative to a full breakdown table when a claim just needs a one-line
 * justification. */
export default function EvidenceDrawer({ trigger = "Why?", children }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="text-sm">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="font-medium text-accent hover:underline">
        {trigger} {open ? "−" : "+"}
      </button>
      {open && <div className="mt-2 rounded-lg border border-border bg-surface-sunken p-3">{children}</div>}
    </div>
  );
}

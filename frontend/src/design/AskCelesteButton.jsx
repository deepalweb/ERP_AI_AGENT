import { ArrowRight } from "lucide-react";

/** One-click "ask Celeste about this" action -- opens the chat overlay and
 * sends `question` immediately (App.jsx's askCeleste()), instead of making
 * someone copy a number off a tile and paste it into chat themselves.
 * Renders nothing when the page wasn't given onAskCeleste, so a route can
 * adopt this one tile/card at a time without every call site needing its
 * own guard. */
export default function AskCelesteButton({ onAskCeleste, question, label = "Ask Celeste", className = "mt-1" }) {
  if (!onAskCeleste) return null;
  return (
    <button
      type="button"
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onAskCeleste(question);
      }}
      className={`inline-flex items-center gap-1 text-[11px] font-medium text-accent hover:underline ${className}`}
    >
      {label} <ArrowRight className="h-3 w-3" strokeWidth={2} />
    </button>
  );
}

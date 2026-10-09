import { useState } from "react";
import { CATEGORIES, getFrequentQuestions } from "./suggestionData";

function ChipRow({ questions, onPick }) {
  return (
    <div className="flex flex-wrap gap-2">
      {questions.map((q) => (
        <button
          key={q.text}
          type="button"
          onClick={() => onPick(q.text)}
          className="rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-foreground-strong hover:border-accent-border hover:text-accent"
        >
          {q.text}
        </button>
      ))}
    </div>
  );
}

/** Was built, never mounted -- see AskCelesteOverlay.jsx's empty-conversation
 * block. onPick sends the question immediately (same path as a follow-up
 * chip), not just a prefill, so this is a genuine shortcut, not busywork. */
export default function SuggestedQuestions({ conversations, onPick }) {
  const [activeCategory, setActiveCategory] = useState(CATEGORIES[0].id);
  const frequent = getFrequentQuestions(conversations);
  const current = CATEGORIES.find((c) => c.id === activeCategory) ?? CATEGORIES[0];

  return (
    <div className="mt-6 space-y-5">
      {frequent.length > 0 && (
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-foreground-subtle">Frequently asked</div>
          <div className="mt-2">
            <ChipRow questions={frequent} onPick={onPick} />
          </div>
        </div>
      )}

      <div>
        <div className="flex flex-wrap gap-1.5">
          {CATEGORIES.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setActiveCategory(c.id)}
              className={`rounded-full px-3 py-1 text-xs font-medium ${
                c.id === activeCategory ? "bg-accent-soft text-accent" : "text-foreground/60 hover:text-foreground"
              }`}
            >
              {c.emoji} {c.label}
            </button>
          ))}
        </div>
        <div className="mt-3">
          <ChipRow questions={current.questions} onPick={onPick} />
        </div>
      </div>
    </div>
  );
}

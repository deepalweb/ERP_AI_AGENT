import { useState } from "react";
import { CATEGORIES, getFrequentQuestions } from "./suggestionData";

export default function SuggestedQuestions({ conversations, onPick }) {
  const [activeCategory, setActiveCategory] = useState(CATEGORIES[0].id);
  const frequent = getFrequentQuestions(conversations);
  const current = CATEGORIES.find((c) => c.id === activeCategory) ?? CATEGORIES[0];

  return (
    <div className="suggested-questions">
      <p className="empty-state">Ask something like &ldquo;How were sales last month?&rdquo;</p>

      {frequent.length > 0 && (
        <div className="suggested-group">
          <div className="suggested-group-label">Frequently asked</div>
          <div className="suggested-chip-row">
            {frequent.map((q) => (
              <button key={q.text} type="button" className="suggested-chip" onClick={() => onPick(q.text)}>
                {q.text}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="suggested-category-row">
        {CATEGORIES.map((c) => (
          <button
            key={c.id}
            type="button"
            className={`suggested-category-chip${c.id === activeCategory ? " suggested-category-chip-active" : ""}`}
            onClick={() => setActiveCategory(c.id)}
          >
            {c.emoji} {c.label}
          </button>
        ))}
      </div>

      <div className="suggested-chip-row">
        {current.questions.map((q) => (
          <button key={q.text} type="button" className="suggested-chip" onClick={() => onPick(q.text)}>
            {q.text}
          </button>
        ))}
      </div>
    </div>
  );
}

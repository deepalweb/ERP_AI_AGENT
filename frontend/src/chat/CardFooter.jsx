import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";

/** Ask Celeste V2: the one navigation affordance a structured card can
 * carry -- deliberately separate from the FOLLOW_UPS chip row (which
 * re-asks a question; this routes away from chat entirely). Never
 * rendered by the model: `relatedCaseId` is resolved server-side,
 * deterministically, before the model sees the tool result (see agent.py's
 * _investigate_metric) -- this component only decides how to display an
 * identifier it's handed, never generates or guesses one.
 *
 * Wording is deliberate: "Tracked case available" / "Open tracked case,"
 * never "this is the tracked case" -- the card itself stays a live
 * analysis; the case is the separate, preserved record it may point to. */
export default function CardFooter({ relatedCaseId }) {
  return (
    <div className="tool-card-footer">
      {relatedCaseId ? (
        <>
          <span className="tool-card-footer-label">Tracked case available</span>
          <Link to={`/investigate/${encodeURIComponent(relatedCaseId)}`} className="tool-card-footer-link">
            Open tracked case <ArrowRight size={12} />
          </Link>
        </>
      ) : (
        <span className="tool-card-footer-label">No tracked case yet</span>
      )}
    </div>
  );
}

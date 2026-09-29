import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchCases } from "../api";
import {
  CaseOutcomeCard,
  PageHeading,
  Pagination,
  ParallelTracks,
  QueryState,
} from "../design/Management";

/** Outcomes: case-primary. Answers "what happened to this business
 * problem after we started tracking it" -- not "which actions have
 * measurements attached" (that's Actions' job; see its own page
 * responsibility line). One query (/cases), which already returns each
 * case's most recently updated linked action joined server-side (see
 * main.py's /cases route) -- no N-per-case fetching. */
export default function Outcomes() {
  const [caseStatus, setCaseStatus] = useState("resolved");
  const [offset, setOffset] = useState(0);
  const cases = useQuery({
    queryKey: ["cases", "outcomes", caseStatus, offset],
    queryFn: () => fetchCases({ status: caseStatus, offset, limit: 20 }),
  });
  return (
    <div className="workspace-page">
      <PageHeading
        title="Outcomes"
        description="What happened to the business conditions behind each case, and what management work was associated with them."
      />
      <ParallelTracks />
      <section className="mt-8">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Business results</p>
            <h2>Case resolution</h2>
          </div>
          <select
            aria-label="Case resolution status"
            value={caseStatus}
            onChange={(e) => {
              setCaseStatus(e.target.value);
              setOffset(0);
            }}
          >
            <option value="resolved">Resolved cases</option>
            <option value="verification_pending">Verification pending</option>
            <option value="open">Open</option>
            <option value="reopened">Reopened</option>
            <option value="dismissed">Dismissed</option>
            <option value="">All cases</option>
          </select>
        </div>
        <QueryState query={cases} empty="No cases in this state yet." />
        <div className="grid gap-4 xl:grid-cols-2">
          {cases.data?.status === "ok" &&
            cases.data.items.map((item) => (
              <CaseOutcomeCard key={item.case_id} item={item} />
            ))}
        </div>
        <Pagination data={cases.data} offset={offset} onChange={setOffset} />
      </section>
    </div>
  );
}

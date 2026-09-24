import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchCases, fetchManagementActions } from "../api";
import {
  CaseSummary,
  PageHeading,
  Pagination,
  QueryState,
  WorkItem,
} from "../design/Management";

export default function Outcomes() {
  const [caseStatus, setCaseStatus] = useState("resolved");
  const [outcomeStatus, setOutcomeStatus] = useState("");
  const [caseOffset, setCaseOffset] = useState(0);
  const [actionOffset, setActionOffset] = useState(0);
  const cases = useQuery({
    queryKey: ["cases", "outcomes", caseStatus, caseOffset],
    queryFn: () =>
      fetchCases({ status: caseStatus, offset: caseOffset, limit: 20 }),
  });
  const actions = useQuery({
    queryKey: ["management", "outcomes", outcomeStatus, actionOffset],
    queryFn: () =>
      fetchManagementActions({
        outcome_status: outcomeStatus,
        offset: actionOffset,
        limit: 20,
      }),
  });
  return (
    <div className="workspace-page">
      <PageHeading
        title="Outcomes"
        description="Follow the evidence from work completed to business results verified."
      />
      <div className="outcome-explainer">
        <div>
          <span className="eyebrow">01 / Work</span>
          <h2>Action completed</h2>
          <p>A manager has finished the work.</p>
        </div>
        <div>
          <span className="eyebrow">02 / Measurement</span>
          <h2>Target achieved</h2>
          <p>A recorded measurement met the declared target.</p>
        </div>
        <div>
          <span className="eyebrow">03 / Business</span>
          <h2>Case resolved</h2>
          <p>The case passed its own resolution policy.</p>
        </div>
      </div>
      <section className="mt-8">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Business results</p>
            <h2>Case resolution</h2>
          </div>
          <select
            aria-label="Case outcome status"
            value={caseStatus}
            onChange={(e) => {
              setCaseStatus(e.target.value);
              setCaseOffset(0);
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
        <div className="panel overflow-hidden">
          {cases.data?.status === "ok" &&
            cases.data.items.map((item) => (
              <CaseSummary key={item.case_id} item={item} />
            ))}
        </div>
        <Pagination
          data={cases.data}
          offset={caseOffset}
          onChange={setCaseOffset}
        />
      </section>
      <section className="mt-8">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Individual work items</p>
            <h2>Action measurements</h2>
          </div>
          <select
            aria-label="Action outcome status"
            value={outcomeStatus}
            onChange={(e) => {
              setOutcomeStatus(e.target.value);
              setActionOffset(0);
            }}
          >
            <option value="">All actions</option>
            <option value="pending">Pending verification</option>
            <option value="blocked">Blocked verification</option>
            <option value="inconclusive">Inconclusive</option>
            <option value="met">Target met</option>
            <option value="not_met">Target not met</option>
          </select>
        </div>
        <QueryState
          query={actions}
          empty="No actions match this measurement state."
        />
        <div className="grid gap-4 xl:grid-cols-2">
          {actions.data?.status === "ok" &&
            actions.data.items.map((action) => (
              <WorkItem key={action.finding_id} action={action} />
            ))}
        </div>
        <Pagination
          data={actions.data}
          offset={actionOffset}
          onChange={setActionOffset}
        />
      </section>
    </div>
  );
}

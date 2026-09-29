import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchManagementActions } from "../api";
import Button from "../design/Button";
import { DOMAIN_LABEL, DOMAIN_ORDER } from "../design/domains";
import { OUTCOME_STATUS_META, OUTCOME_VERDICT_META } from "../design/outcomeStatus";
import {
  PageHeading,
  Pagination,
  QueryState,
  WorkItem,
} from "../design/Management";

// Single source of truth for this filter's labels -- the exact same metas
// WorkItem's own outcome badge renders, so the dropdown option a manager
// picks always reads identically to the badge it filters for (previously
// this filter lived on Outcomes.jsx with its own hand-typed "Blocked
// verification" label, which drifted from the badge's real wording).
const OUTCOME_FILTERS = [
  { value: "", label: "All outcomes" },
  ...Object.entries(OUTCOME_STATUS_META).map(([value, meta]) => ({ value, label: meta.label })),
  ...Object.entries(OUTCOME_VERDICT_META).map(([value, meta]) => ({ value, label: meta.label })),
];

export default function Actions() {
  const [status, setStatus] = useState("");
  const [relevance, setRelevance] = useState("");
  const [domain, setDomain] = useState("");
  const [owner, setOwner] = useState("");
  const [outcomeStatus, setOutcomeStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["management", "actions", status, relevance, domain, owner, outcomeStatus, offset],
    queryFn: () =>
      fetchManagementActions({
        status,
        relevant: relevance,
        domain,
        owner,
        outcome_status: outcomeStatus,
        offset,
        limit: 20,
      }),
  });
  return (
    <div className="workspace-page">
      <PageHeading
        title="Actions"
        description="Every work item. A clear owner, a measurable objective, and a path back to the evidence."
      />
      <div className="filter-bar">
        <div className="filter-tabs">
          {[
            ["", "All actions"],
            ["not_started", "Not started"],
            ["in_progress", "In progress"],
            ["completed", "Completed"],
          ].map(([value, label]) => (
            <Button
              key={value}
              active={status === value}
              onClick={() => {
                setStatus(value);
                setOffset(0);
              }}
            >
              {label}
            </Button>
          ))}
        </div>
        <select
          aria-label="Action relevance"
          value={relevance}
          onChange={(e) => {
            setRelevance(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All relevance</option>
          <option value="true">Relevant</option>
          <option value="false">Not relevant</option>
        </select>
        <select
          aria-label="Domain"
          value={domain}
          onChange={(e) => {
            setDomain(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All domains</option>
          {DOMAIN_ORDER.map((d) => (
            <option key={d} value={d}>{DOMAIN_LABEL[d]}</option>
          ))}
        </select>
        <input
          type="text"
          aria-label="Owner"
          placeholder="Owner"
          value={owner}
          onChange={(e) => {
            setOwner(e.target.value);
            setOffset(0);
          }}
        />
        <select
          aria-label="Outcome"
          value={outcomeStatus}
          onChange={(e) => {
            setOutcomeStatus(e.target.value);
            setOffset(0);
          }}
        >
          {OUTCOME_FILTERS.map(({ value, label }) => (
            <option key={value || "all"} value={value}>{label}</option>
          ))}
        </select>
      </div>
      <QueryState
        query={query}
        empty="No actions match this view. Record an action from a case in Investigate."
      />
      {query.data?.status === "ok" && (
        <>
          <div className="grid gap-4 xl:grid-cols-2">
            {query.data.items.map((action) => (
              <WorkItem key={action.finding_id} action={action} />
            ))}
          </div>
          <Pagination data={query.data} offset={offset} onChange={setOffset} />
        </>
      )}
    </div>
  );
}

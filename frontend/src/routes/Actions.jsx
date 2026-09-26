import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchManagementActions } from "../api";
import Button from "../design/Button";
import { DOMAIN_LABEL, DOMAIN_ORDER } from "../design/domains";
import {
  PageHeading,
  Pagination,
  QueryState,
  WorkItem,
} from "../design/Management";

export default function Actions() {
  const [status, setStatus] = useState("");
  const [relevance, setRelevance] = useState("");
  const [domain, setDomain] = useState("");
  const [owner, setOwner] = useState("");
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["management", "actions", status, relevance, domain, owner, offset],
    queryFn: () =>
      fetchManagementActions({
        status,
        relevant: relevance,
        domain,
        owner,
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

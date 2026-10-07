import RankedBarChart from "../charts/RankedBarChart";
import { formatCurrency, formatNumber } from "../format";
import { DOMAIN_LABEL } from "./domains";
import { exposureByDomain } from "./overviewMath";

export function ExposureByDomain({ impacts }) {
  const rows = exposureByDomain(impacts ?? []).filter((row) => row.quantifiedCases > 0);
  if (!rows.length) return null;
  const data = rows.map((row) => ({
    name: DOMAIN_LABEL[row.domain] ?? row.domain,
    value: row.exposure,
    coverage: row.coverage,
  }));
  return (
    <section aria-label="Exposure by domain" className="rounded-xl border border-border bg-surface p-4">
      <h3 className="text-sm font-semibold text-foreground-strong">Recorded case exposure by domain</h3>
      <p className="mt-1 text-xs text-foreground-subtle">
        Warehouse transfer discrepancies only, estimated at current catalog cost. Cases overlap and this is not confirmed loss.
        Coverage is the share of affected units that had a usable cost. Other case types use different bases and are not combined here.
      </p>
      <div className="mt-3">
        <RankedBarChart
          data={data}
          nameKey="name"
          valueKey="value"
          labelWidth={180}
          rowHeight={40}
          formatValue={(v) => {
            const row = data.find((d) => d.value === v);
            return row?.coverage == null ? `${formatCurrency(v)} · coverage not established` : `${formatCurrency(v)} · ${row.coverage}% coverage`;
          }}
        />
      </div>
    </section>
  );
}
